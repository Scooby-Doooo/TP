"""Debug Adapter Protocol server over stdio. Run with: python -m sipl.dap

A thin protocol layer: parsing, execution and stop points all come from the core.
SIPL has no procedures, so there is one thread and one stack frame, and all three
step requests (next, stepIn, stepOut) advance to the next stop point.
"""

from __future__ import annotations

import json
import os
import sys
from bisect import bisect_left
from collections.abc import Iterator
from typing import Any, BinaryIO

from sipl.ast import Program, SourceSpan
from sipl.diagnostics import SiplError, SiplRuntimeError
from sipl.interpreter import (
    DEFAULT_MAX_STEPS,
    Event,
    Finished,
    evaluate,
    execute,
    is_stop_point,
    parse_state,
    stop_point_spans,
)
from sipl.parser import parse, parse_expression

_THREAD_ID = 1
_FRAME_ID = 1
_STATE_REFERENCE = 1


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class DebugSession:
    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self.reader = reader
        self.writer = writer
        self.seq = 0
        self.running = True
        self.program_path = ""
        self.program: Program | None = None
        self.state: dict[str, int] = {}
        self.events: Iterator[Event] = iter(())
        self.span: SourceSpan | None = None
        self.breakpoints: set[int] = set()
        self.stop_on_entry = False
        self.no_debug = False
        self.exit_code = 0

    # Protocol plumbing

    def _send(self, message: dict[str, Any]) -> None:
        self.seq += 1
        body = json.dumps({"seq": self.seq, **message}).encode()
        self.writer.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        self.writer.flush()

    def _event(self, event: str, body: dict[str, Any] | None = None) -> None:
        self._send({"type": "event", "event": event, "body": body or {}})

    def _read(self) -> dict[str, Any] | None:
        length = None
        while True:
            line = self.reader.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value)
        if length is None:
            return None
        message: dict[str, Any] = json.loads(self.reader.read(length))
        return message

    def serve(self) -> None:
        while self.running and (request := self._read()) is not None:
            self._dispatch(request)

    def _dispatch(self, request: dict[str, Any]) -> None:
        command = request.get("command", "")
        reply = {"type": "response", "request_seq": request.get("seq", 0), "command": command}
        handler = getattr(self, f"on_{command}", None)
        if handler is None:
            self._send({**reply, "success": False, "message": f"Не підтримується: {command}"})
            return
        after = None
        try:
            result = handler(request.get("arguments") or {})
            if isinstance(result, tuple):
                result, after = result
        except (SiplError, ValueError, OSError, KeyError, TypeError) as error:
            self._send({**reply, "success": False, "message": _error_text(error)})
            return
        self._send({**reply, "success": True, "body": result or {}})
        if after is not None:
            after()

    # Lifecycle

    def on_initialize(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"supportsConfigurationDoneRequest": True, "supportsEvaluateForHovers": True}

    def on_launch(self, args: dict[str, Any]) -> tuple[None, Any]:
        self.program_path = args["program"]
        with open(self.program_path, encoding="utf-8-sig") as file:
            self.program = parse(file.read())
        self.state = parse_state(args.get("input", ""))
        self.stop_on_entry = bool(args.get("stopOnEntry", False))
        self.no_debug = bool(args.get("noDebug", False))
        self.events = execute(
            self.program, self.state, max_steps=args.get("maxSteps", DEFAULT_MAX_STEPS)
        )
        return None, lambda: self._event("initialized")

    def on_setBreakpoints(self, args: dict[str, Any]) -> dict[str, Any]:
        requested = [item["line"] for item in args.get("breakpoints", [])]
        matches = self.program is not None and _same_file(
            args.get("source", {}).get("path", ""), self.program_path
        )
        if not matches or self.program is None:
            return {"breakpoints": [{"verified": False, "line": line} for line in requested]}

        lines = sorted({span.start_line for span in stop_point_spans(self.program)})
        result = []
        self.breakpoints = set()
        for line in requested:
            index = bisect_left(lines, line)  # a blank or `end` line binds to the next stop point
            if index == len(lines):
                result.append(
                    {"verified": False, "line": line, "message": "Після цього рядка немає коду"}
                )
            else:
                self.breakpoints.add(lines[index])
                result.append({"verified": True, "line": lines[index]})
        return {"breakpoints": result}

    def on_setExceptionBreakpoints(self, args: dict[str, Any]) -> None:
        return None

    def on_configurationDone(self, args: dict[str, Any]) -> tuple[None, Any]:
        return None, lambda: self._resume(step=self.stop_on_entry, reason="entry")

    def on_disconnect(self, args: dict[str, Any]) -> None:
        self.running = False

    def on_terminate(self, args: dict[str, Any]) -> tuple[None, Any]:
        return None, lambda: self._event("terminated")

    # Execution control

    def _resume(self, step: bool, reason: str = "step") -> None:
        while True:
            try:
                event = next(self.events)
            except StopIteration:
                self._event("exited", {"exitCode": self.exit_code})
                self._event("terminated")
                return
            except SiplRuntimeError as error:
                self.exit_code = 1
                self.span = error.diagnostic.span
                message = error.diagnostic.message
                self._event("output", {"category": "stderr", "output": f"{message}\n"})
                self._stop("exception", error.diagnostic.code, message)
                return

            if isinstance(event, Finished):
                lines = "".join(f"{name} = {event.state[name]}\n" for name in sorted(event.state))
                self._event("output", {"category": "stdout", "output": lines})
            elif is_stop_point(event) and not self.no_debug:
                self.span = event.span
                if step:
                    self._stop(reason)
                    return
                if event.span.start_line in self.breakpoints:
                    self._stop("breakpoint")
                    return

    def _stop(self, reason: str, description: str = "", text: str = "") -> None:
        body = {"reason": reason, "threadId": _THREAD_ID, "allThreadsStopped": True}
        if text:
            body |= {"description": description, "text": text}
        self._event("stopped", body)

    def on_continue(self, args: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        return {"allThreadsContinued": True}, lambda: self._resume(step=False)

    def on_next(self, args: dict[str, Any]) -> tuple[None, Any]:
        return None, lambda: self._resume(step=True)

    on_stepIn = on_next
    on_stepOut = on_next

    def on_pause(self, args: dict[str, Any]) -> None:
        # Requests are handled between runs, and every run ends at the step limit at the latest.
        return None

    # Inspection

    def on_threads(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"threads": [{"id": _THREAD_ID, "name": "main"}]}

    def on_stackTrace(self, args: dict[str, Any]) -> dict[str, Any]:
        span = self.span
        if span is None:
            return {"stackFrames": [], "totalFrames": 0}
        frame = {
            "id": _FRAME_ID,
            "name": "program",
            "source": {
                "name": os.path.basename(self.program_path),
                "path": os.path.abspath(self.program_path),
            },
            "line": span.start_line,
            "column": span.start_column,
            "endLine": span.end_line,
            "endColumn": span.end_column,
        }
        return {"stackFrames": [frame], "totalFrames": 1}

    def on_scopes(self, args: dict[str, Any]) -> dict[str, Any]:
        scope = {"name": "Стан", "variablesReference": _STATE_REFERENCE, "expensive": False}
        return {"scopes": [scope]}

    def on_variables(self, args: dict[str, Any]) -> dict[str, Any]:
        items = [
            {"name": name, "value": str(self.state[name]), "type": "int", "variablesReference": 0}
            for name in sorted(self.state)
        ]
        return {"variables": items}

    def on_evaluate(self, args: dict[str, Any]) -> dict[str, Any]:
        value = evaluate(parse_expression(args["expression"]), self.state)
        text = str(value).lower() if isinstance(value, bool) else str(value)
        return {"result": text, "variablesReference": 0}


def _error_text(error: Exception) -> str:
    if isinstance(error, SiplError):
        span = error.diagnostic.span
        where = f"{span.start_line}:{span.start_column}: " if span else ""
        return f"{where}{error.diagnostic.message}"
    return str(error)


def main() -> None:
    DebugSession(sys.stdin.buffer, sys.stdout.buffer).serve()


if __name__ == "__main__":
    main()
