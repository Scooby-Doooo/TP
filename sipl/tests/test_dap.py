"""Protocol tests of the debug adapter: scripted sessions, plus one real subprocess."""

import io
import json
import subprocess
import sys

from sipl.dap import DebugSession

PROGRAM = """begin
 R := 1;
 while Y > 0 do
 begin
  R := R * X;
  Y := Y - 1
 end
end
"""


def frame(message):
    body = json.dumps(message).encode()
    return b"Content-Length: %d\r\n\r\n%s" % (len(body), body)


def parse_frames(data):
    messages = []
    while data:
        header, _, rest = data.partition(b"\r\n\r\n")
        length = int(header.split(b":")[1])
        messages.append(json.loads(rest[:length]))
        data = rest[length:]
    return messages


def session(source, *requests, **launch):
    """Replay requests through the adapter and return everything it sent."""
    script = [
        ("initialize", {"adapterID": "sipl"}),
        ("launch", {"program": str(source), **launch}),
        *requests,
    ]
    stdin = b"".join(
        frame({"seq": seq, "type": "request", "command": command, "arguments": arguments})
        for seq, (command, arguments) in enumerate(script, 1)
    )
    stdout = io.BytesIO()
    DebugSession(io.BytesIO(stdin), stdout).serve()
    return parse_frames(stdout.getvalue())


def responses(messages, command):
    return [m for m in messages if m["type"] == "response" and m["command"] == command]


def events(messages, name):
    return [m for m in messages if m["type"] == "event" and m["event"] == name]


def breakpoints(source, *lines):
    return (
        "setBreakpoints",
        {"source": {"path": str(source)}, "breakpoints": [{"line": line} for line in lines]},
    )


CONFIG_DONE = ("configurationDone", {})
VARIABLES = ("variables", {"variablesReference": 1})
STACK = ("stackTrace", {"threadId": 1})


def state_of(response):
    return {v["name"]: v["value"] for v in response["body"]["variables"]}


def test_run_to_end_without_breakpoints(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    messages = session(source, CONFIG_DONE, input="X=2 Y=3")
    assert responses(messages, "initialize")[0]["body"]["supportsConfigurationDoneRequest"]
    assert len(events(messages, "initialized")) == 1
    assert not events(messages, "stopped")
    assert events(messages, "exited")[0]["body"]["exitCode"] == 0
    assert len(events(messages, "terminated")) == 1
    assert "R = 8" in events(messages, "output")[0]["body"]["output"]


def test_breakpoint_hits_with_current_state_on_each_iteration(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    messages = session(
        source,
        breakpoints(source, 5),
        CONFIG_DONE,
        STACK,
        VARIABLES,
        ("continue", {"threadId": 1}),
        VARIABLES,
        ("continue", {"threadId": 1}),
        input="X=2 Y=2",
    )
    assert responses(messages, "setBreakpoints")[0]["body"]["breakpoints"] == [
        {"verified": True, "line": 5}
    ]
    stops = events(messages, "stopped")
    assert [stop["body"]["reason"] for stop in stops] == ["breakpoint", "breakpoint"]
    frame_ = responses(messages, "stackTrace")[0]["body"]["stackFrames"][0]
    assert (frame_["line"], frame_["column"], frame_["endLine"], frame_["endColumn"]) == (
        5,
        3,
        5,
        13,
    )
    assert frame_["source"]["path"] == str(source)
    states = [state_of(r) for r in responses(messages, "variables")]
    assert states == [{"R": "1", "X": "2", "Y": "2"}, {"R": "2", "X": "2", "Y": "1"}]
    assert events(messages, "terminated")


def test_breakpoint_on_while_stops_every_iteration(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    messages = session(
        source,
        breakpoints(source, 3),
        CONFIG_DONE,
        ("continue", {"threadId": 1}),
        ("continue", {"threadId": 1}),
        input="X=2 Y=1",
    )
    assert len(events(messages, "stopped")) == 2


def test_breakpoints_bind_to_next_statement_or_stay_unverified(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    messages = session(source, breakpoints(source, 1, 4, 7), CONFIG_DONE, input="X=2 Y=0")
    assert responses(messages, "setBreakpoints")[0]["body"]["breakpoints"][:2] == [
        {"verified": True, "line": 2},
        {"verified": True, "line": 5},
    ]
    assert responses(messages, "setBreakpoints")[0]["body"]["breakpoints"][2]["verified"] is False


def test_breakpoints_in_another_file_are_unverified(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    messages = session(source, breakpoints(tmp_path / "other.sipl", 2), input="X=2 Y=0")
    assert responses(messages, "setBreakpoints")[0]["body"]["breakpoints"] == [
        {"verified": False, "line": 2}
    ]


def test_stop_on_entry_and_stepping_follow_stop_points(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    step = ("next", {"threadId": 1})
    messages = session(
        source,
        CONFIG_DONE,
        STACK,
        step,
        STACK,
        ("stepIn", {"threadId": 1}),
        STACK,
        ("stepOut", {"threadId": 1}),
        STACK,
        input="X=2 Y=1",
        stopOnEntry=True,
    )
    assert [stop["body"]["reason"] for stop in events(messages, "stopped")] == [
        "entry",
        "step",
        "step",
        "step",
    ]
    lines = [r["body"]["stackFrames"][0]["line"] for r in responses(messages, "stackTrace")]
    assert lines == [2, 3, 5, 6]


def test_evaluate_expressions_in_current_state(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    def evaluate(text):
        return ("evaluate", {"expression": text, "frameId": 1})

    messages = session(
        source,
        breakpoints(source, 6),
        CONFIG_DONE,
        evaluate("R + X * 10"),
        evaluate("R > 100"),
        evaluate("Q"),
        evaluate("R +"),
        input="X=3 Y=1",
    )
    results = responses(messages, "evaluate")
    assert results[0]["body"]["result"] == "33"
    assert results[1]["body"]["result"] == "false"
    assert results[2]["success"] is False and "Q" in results[2]["message"]
    assert results[3]["success"] is False


def test_runtime_error_stops_as_exception_then_terminates(tmp_path):
    source = tmp_path / "bad.sipl"
    source.write_text("begin\n X := 1;\n Y := Q\nend", encoding="utf-8")
    messages = session(source, CONFIG_DONE, STACK, VARIABLES, ("continue", {"threadId": 1}))
    stop = events(messages, "stopped")[0]["body"]
    assert stop["reason"] == "exception" and "Q" in stop["text"]
    assert responses(messages, "stackTrace")[0]["body"]["stackFrames"][0]["line"] == 3
    assert state_of(responses(messages, "variables")[0]) == {"X": "1"}
    assert events(messages, "exited")[0]["body"]["exitCode"] == 1


def test_step_limit_is_reported_in_debugger(tmp_path):
    source = tmp_path / "loop.sipl"
    source.write_text("begin while 1 > 0 do skip end", encoding="utf-8")
    messages = session(source, CONFIG_DONE, maxSteps=10)
    assert events(messages, "stopped")[0]["body"]["description"] == "step-limit"


def test_syntax_error_fails_launch_with_position(tmp_path):
    source = tmp_path / "bad.sipl"
    source.write_text("begin\n X := ;\nend", encoding="utf-8")
    launch = responses(session(source), "launch")[0]
    assert launch["success"] is False and launch["message"].startswith("2:7:")


def test_missing_file_and_bad_input_fail_launch(tmp_path):
    assert responses(session(tmp_path / "none.sipl"), "launch")[0]["success"] is False
    source = tmp_path / "ok.sipl"
    source.write_text("begin skip end", encoding="utf-8")
    assert responses(session(source, input="X"), "launch")[0]["success"] is False


def test_no_debug_ignores_breakpoints(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    messages = session(source, breakpoints(source, 5), CONFIG_DONE, input="X=2 Y=1", noDebug=True)
    assert not events(messages, "stopped")


def test_unknown_request_and_lifecycle_requests(tmp_path):
    source = tmp_path / "ok.sipl"
    source.write_text("begin skip end", encoding="utf-8")
    messages = session(
        source,
        ("exceptionInfo", {}),
        ("threads", {}),
        ("scopes", {"frameId": 1}),
        STACK,
        ("setExceptionBreakpoints", {"filters": []}),
        ("pause", {"threadId": 1}),
        ("terminate", {}),
        ("disconnect", {}),
        ("threads", {}),
    )
    assert responses(messages, "exceptionInfo")[0]["success"] is False
    assert responses(messages, "threads")[0]["body"]["threads"] == [{"id": 1, "name": "main"}]
    assert responses(messages, "scopes")[0]["body"]["scopes"][0]["variablesReference"] == 1
    assert responses(messages, "stackTrace")[0]["body"]["stackFrames"] == []
    assert events(messages, "terminated")
    assert len(responses(messages, "threads")) == 1  # nothing is handled after disconnect


def test_adapter_as_a_separate_process(tmp_path):
    source = tmp_path / "power.sipl"
    source.write_text(PROGRAM, encoding="utf-8")
    script = [
        ("initialize", {"adapterID": "sipl"}),
        ("launch", {"program": str(source), "input": "X=2 Y=1"}),
        breakpoints(source, 5),
        CONFIG_DONE,
        VARIABLES,
        ("continue", {"threadId": 1}),
        ("disconnect", {}),
    ]
    stdin = b"".join(
        frame({"seq": seq, "type": "request", "command": command, "arguments": arguments})
        for seq, (command, arguments) in enumerate(script, 1)
    )
    result = subprocess.run(
        [sys.executable, "-m", "sipl.dap"], input=stdin, capture_output=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    messages = parse_frames(result.stdout)
    assert state_of(responses(messages, "variables")[0]) == {"R": "1", "X": "2", "Y": "1"}
    assert events(messages, "terminated")
