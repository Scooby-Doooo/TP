"""Command line interface: parse, check, run, trace. The JSON format is described in README."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from sipl.ast import Program, to_dict
from sipl.diagnostics import Diagnostic, SiplRuntimeError, SiplSyntaxError
from sipl.interpreter import (
    DEFAULT_MAX_STEPS,
    AfterStatement,
    BeforeStatement,
    ConditionChecked,
    Event,
    Finished,
    VariableChanged,
    event_to_dict,
    execute,
    parse_state,
)
from sipl.parser import parse

SCHEMA_VERSION = 1
EXIT_OK = 0
EXIT_SYNTAX = 1
EXIT_USAGE = 2
EXIT_RUNTIME = 3


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("очікувалось додатне ціле число")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sipl", description="Інструменти мови SIPL")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("parse", "вивести AST програми"),
        ("check", "перевірити синтаксис"),
        ("run", "виконати програму й вивести кінцевий стан"),
        ("trace", "виконати програму й вивести всі події"),
    ):
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument("file", help="файл .sipl")
        command.add_argument("--json", action="store_true", help="структурований вивід")
        if name in ("run", "trace"):
            command.add_argument("--input", default="", help='початковий стан, напр. "X=2 Y=10"')
            command.add_argument(
                "--max-steps",
                type=_positive_int,
                default=DEFAULT_MAX_STEPS,
                help="ліміт кроків виконання (типово %(default)s)",
            )
    return parser


def _emit_json(command: str, ok: bool, diagnostics: list[Diagnostic], **fields: Any) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "ok": ok,
        **fields,
        "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _failure_fields(command: str) -> dict[str, Any]:
    """Result fields of a command that stopped before producing them."""
    fields: dict[str, dict[str, Any]] = {
        "parse": {"ast": None},
        "check": {},
        "run": {"state": None, "steps": None},
        "trace": {"events": [], "state": None, "steps": None},
    }
    return fields[command]


def _print_ast(node: Any, indent: int = 0, label: str = "") -> None:
    """Print an AST tree from its JSON form, so text and JSON can never disagree."""
    span = node["span"]
    scalars = "".join(
        f" {key}={value}"
        for key, value in node.items()
        if key not in ("type", "span") and not isinstance(value, (dict, list))
    )
    position = f"{span['start_line']}:{span['start_column']}"
    print(f"{'  ' * indent}{label}{node['type']}{scalars} @{position}")
    for key, value in node.items():
        if isinstance(value, dict) and key != "span":
            _print_ast(value, indent + 1, f"{key}: ")
        elif isinstance(value, list):
            for item in value:
                _print_ast(item, indent + 1)


def _print_state(state: dict[str, int]) -> None:
    for name in sorted(state):
        print(f"{name} = {state[name]}")


def _format_event(event: Event) -> str:
    position = f"{event.span.start_line}:{event.span.start_column}"
    match event:
        case BeforeStatement(statement=statement) | AfterStatement(statement=statement):
            details = statement
        case VariableChanged(name=name, old=old, new=new):
            details = f"{name}: {'—' if old is None else old} -> {new}"
        case ConditionChecked(value=value):
            details = "true" if value else "false"
        case Finished(steps=steps):
            details = f"кроків: {steps}"
    return f"{position} {type(event).__name__} {details}"


def _execute(args: argparse.Namespace, program: Program) -> int:
    try:
        state = parse_state(args.input)
    except ValueError as error:
        return _fail(args, Diagnostic("invalid-input", str(error)), EXIT_USAGE)

    tracing = args.command == "trace"
    events: list[dict[str, Any]] = []
    steps = None
    try:
        for event in execute(program, state, max_steps=args.max_steps):
            if isinstance(event, Finished):
                steps = event.steps
            if tracing and args.json:
                events.append(event_to_dict(event))
            elif tracing:
                print(_format_event(event))
    except SiplRuntimeError as error:
        return _fail(
            args,
            error.diagnostic,
            EXIT_RUNTIME,
            {"state": state, "steps": None, **({"events": events} if tracing else {})},
        )

    if args.json:
        fields = {"state": state, "steps": steps, **({"events": events} if tracing else {})}
        _emit_json(args.command, True, [], **fields)
    else:
        _print_state(state)
    return EXIT_OK


def _fail(
    args: argparse.Namespace,
    diagnostic: Diagnostic,
    code: int,
    fields: dict[str, Any] | None = None,
) -> int:
    if args.json:
        _emit_json(
            args.command,
            False,
            [diagnostic],
            **(_failure_fields(args.command) if fields is None else fields),
        )
    else:
        print(diagnostic.format(args.file), file=sys.stderr)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Pipes on Windows default to a legacy code page that cannot encode all messages.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    try:
        with open(args.file, encoding="utf-8-sig") as file:
            text = file.read()
    except (OSError, UnicodeDecodeError) as error:
        return _fail(
            args,
            Diagnostic("cannot-read-file", f"Не вдалося прочитати файл: {error}"),
            EXIT_USAGE,
        )

    try:
        program = parse(text)
    except SiplSyntaxError as error:
        return _fail(args, error.diagnostic, EXIT_SYNTAX)

    if args.command == "parse":
        ast = to_dict(program)
        if args.json:
            _emit_json("parse", True, [], ast=ast)
        else:
            _print_ast(ast)
        return EXIT_OK
    if args.command == "check":
        if args.json:
            _emit_json("check", True, [])
        else:
            print(f"{args.file}: синтаксис коректний")
        return EXIT_OK
    return _execute(args, program)


if __name__ == "__main__":
    sys.exit(main())
