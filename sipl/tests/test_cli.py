"""Integration tests of the CLI contract: exit codes and the JSON format."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sipl.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"
POWER = str(EXAMPLES / "power.sipl")
INVALID = str(EXAMPLES / "invalid" / "trailing_semicolon.sipl")


def run_cli(*args):
    """Run the CLI as a real process, the way other tools integrate with it."""
    return subprocess.run(
        [sys.executable, "-m", "sipl.cli", *args],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "cp1251"},
    )


def json_of(capsys, *args):
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def test_process_run_text_output_and_exit_code():
    result = run_cli("run", POWER, "--input", "X=2 Y=10")
    assert result.returncode == 0
    assert result.stdout.split("\n")[:3] == ["R = 1024", "X = 2", "Y = 0"]


def test_process_reports_ukrainian_diagnostics_under_legacy_encoding():
    result = run_cli("check", INVALID)
    assert result.returncode == 1
    assert "помилка[syntax]" in result.stderr
    assert ":3:1:" in result.stderr


def test_process_json_is_parseable():
    result = run_cli("check", POWER, "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "command": "check",
        "ok": True,
        "diagnostics": [],
    }


def test_parse_json_contract(capsys):
    code, data = json_of(capsys, "parse", POWER, "--json")
    assert code == 0
    assert set(data) == {"schema_version", "command", "ok", "ast", "diagnostics"}
    assert data["ast"]["type"] == "Program"
    assert data["ast"]["body"]["statements"][1]["type"] == "While"


def test_parse_text_prints_tree(capsys):
    assert main(["parse", POWER]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Program @1:1")
    assert "Assign name=R @2:2" in out
    assert "condition: Compare op=> @3:8" in out


def test_check_text(capsys):
    assert main(["check", POWER]) == 0
    assert "коректний" in capsys.readouterr().out


def test_syntax_error_json_contract(capsys):
    code, data = json_of(capsys, "parse", INVALID, "--json")
    assert code == 1
    assert data["ok"] is False and data["ast"] is None
    diagnostic = data["diagnostics"][0]
    assert set(diagnostic) == {"code", "message", "span"}
    assert diagnostic["code"] == "syntax"
    assert diagnostic["span"] == {
        "start_line": 3,
        "start_column": 1,
        "end_line": 3,
        "end_column": 4,
    }


def test_run_json_contract(capsys):
    code, data = json_of(capsys, "run", POWER, "--input", "X=3 Y=2", "--json")
    assert code == 0
    assert set(data) == {"schema_version", "command", "ok", "state", "steps", "diagnostics"}
    assert data["state"] == {"R": 9, "X": 3, "Y": 0}
    assert data["steps"] == 8


def test_runtime_error_json_keeps_last_state(capsys):
    code, data = json_of(capsys, "run", POWER, "--input", "Y=1", "--json")
    assert code == 3
    assert data["ok"] is False
    assert data["state"] == {"R": 1, "Y": 1}
    assert data["steps"] is None
    assert data["diagnostics"][0]["code"] == "undefined-variable"


def test_runtime_error_text(capsys):
    assert main(["run", POWER, "--input", "Y=1"]) == 3
    err = capsys.readouterr().err
    assert ":5:" in err and "undefined-variable" in err and "'X'" in err


def test_step_limit_exit_code(capsys, tmp_path):
    source = tmp_path / "loop.sipl"
    source.write_text("begin while 1 > 0 do skip end", encoding="utf-8")
    code, data = json_of(capsys, "run", str(source), "--max-steps", "10", "--json")
    assert code == 3
    assert data["diagnostics"][0]["code"] == "step-limit"


def test_trace_json_contract(capsys):
    code, data = json_of(capsys, "trace", POWER, "--input", "X=2 Y=1", "--json")
    assert code == 0
    assert set(data) == {
        "schema_version",
        "command",
        "ok",
        "events",
        "state",
        "steps",
        "diagnostics",
    }
    kinds = {event["event"] for event in data["events"]}
    assert kinds == {
        "BeforeStatement",
        "AfterStatement",
        "VariableChanged",
        "ConditionChecked",
        "Finished",
    }
    change = next(e for e in data["events"] if e["event"] == "VariableChanged")
    assert set(change) == {"event", "span", "name", "old", "new"}
    assert data["events"][-1]["state"] == {"R": 2, "X": 2, "Y": 0}


def test_trace_text(capsys):
    assert main(["trace", POWER, "--input", "X=2 Y=1"]) == 0
    out = capsys.readouterr().out
    assert "2:2 VariableChanged R: — -> 1" in out
    assert "3:8 ConditionChecked true" in out
    assert "Finished кроків:" in out
    assert out.rstrip().endswith("Y = 0")


def test_failed_trace_json_has_events_so_far(capsys):
    code, data = json_of(capsys, "trace", POWER, "--input", "Y=1", "--json")
    assert code == 3
    assert data["events"] and data["events"][-1]["event"] == "BeforeStatement"


def test_missing_file_exit_code(capsys):
    code, data = json_of(capsys, "check", "no-such-file.sipl", "--json")
    assert code == 2
    assert data["diagnostics"][0]["code"] == "cannot-read-file"
    assert data["diagnostics"][0]["span"] is None


def test_missing_file_text(capsys):
    assert main(["run", "no-such-file.sipl"]) == 2
    assert "cannot-read-file" in capsys.readouterr().err


def test_bad_input_exit_code(capsys):
    code, data = json_of(capsys, "run", POWER, "--input", "X", "--json")
    assert code == 2
    assert data["diagnostics"][0]["code"] == "invalid-input"
    assert data["state"] is None


def test_file_with_bom_is_accepted(tmp_path):
    source = tmp_path / "bom.sipl"
    source.write_bytes(b"\xef\xbb\xbfbegin skip end")
    assert main(["check", str(source)]) == 0


@pytest.mark.parametrize("argv", [[], ["run"], ["run", POWER, "--max-steps", "0"]])
def test_usage_errors_exit_2(argv):
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 2
