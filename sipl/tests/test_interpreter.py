import json
from pathlib import Path

import pytest

from sipl.diagnostics import SiplRuntimeError
from sipl.interpreter import (
    MAX_INT_BITS,
    AfterStatement,
    BeforeStatement,
    ConditionChecked,
    Finished,
    VariableChanged,
    evaluate,
    event_to_dict,
    execute,
    is_stop_point,
    parse_state,
    run,
    stop_point_spans,
)
from sipl.parser import parse, parse_expression

EXAMPLES = Path(__file__).parent.parent / "examples"


def run_text(text, **state):
    return run(parse(text), state)


def run_example(name, **state):
    return run(parse((EXAMPLES / name).read_text(encoding="utf-8")), state)


@pytest.mark.parametrize(
    ("name", "state", "expected"),
    [
        ("power.sipl", {"X": 2, "Y": 10}, {"R": 1024, "Y": 0}),
        ("power.sipl", {"X": 7, "Y": 0}, {"R": 1}),
        ("factorial.sipl", {"N": 5}, {"F": 120}),
        ("gcd.sipl", {"A": 48, "B": 18}, {"G": 6}),
        ("sign.sipl", {"X": -3}, {"S": -1}),
        ("sign.sipl", {"X": 0}, {"S": 0}),
        ("sign.sipl", {"X": 3}, {"S": 1}),
    ],
)
def test_examples(name, state, expected):
    final = run_example(name, **state)
    assert {key: final[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("10 - 3 - 2", 5),
        ("-2 * -3", 6),
        ("2 - -3", 5),
        ("X * X", 16),
        ("X = 4", True),
        ("X = 5", False),
        ("X > 3", True),
        ("X > 4", False),
        ("X = 5 ∨ X = 4", True),
        ("X = 5 or X = 6", False),
        ("¬ X = 5", True),
        ("not X = 4", False),
        ("¬ (X = 5 ∨ X = 4)", False),
        ("¬ X = 4 ∨ X = 4", True),
    ],
)
def test_expression_semantics(expression, expected):
    assert evaluate(parse_expression(expression), {"X": 4}) == expected


def test_if_takes_the_right_branch():
    text = "begin if X > 0 then R := 1 else R := 2 end"
    assert run_text(text, X=1)["R"] == 1
    assert run_text(text, X=0)["R"] == 2


def test_while_with_false_condition_never_runs_body():
    assert run_text("begin while X > 0 do R := 1 end", X=0) == {"X": 0}


def test_skip_and_block_keep_state():
    assert run_text("begin begin skip end end", X=1) == {"X": 1}


def test_integers_are_not_machine_bound():
    assert run_text("begin R := 99999999999 * 99999999999 end")["R"] == 99999999999**2


def test_run_does_not_mutate_input_state():
    state = {"X": 1}
    run(parse("begin X := 2 end"), state)
    assert state == {"X": 1}


def test_undefined_variable_is_a_positioned_error():
    with pytest.raises(SiplRuntimeError) as info:
        run_text("begin\n  R := Q + 1\nend")
    diagnostic = info.value.diagnostic
    assert diagnostic.code == "undefined-variable"
    assert "Q" in diagnostic.message
    assert (diagnostic.span.start_line, diagnostic.span.start_column) == (2, 8)


def test_or_is_strict_about_undefined_operands():
    with pytest.raises(SiplRuntimeError):
        run_text("begin if 1 = 1 ∨ Q = 1 then skip else skip end")


def test_infinite_loop_hits_step_limit():
    with pytest.raises(SiplRuntimeError) as info:
        run(parse("begin while 1 > 0 do skip end"), max_steps=100)
    assert info.value.diagnostic.code == "step-limit"
    assert info.value.diagnostic.span is not None


def test_step_limit_counts_exactly():
    program = parse("begin X := 1; skip end")
    assert run(program, max_steps=2) == {"X": 1}
    with pytest.raises(SiplRuntimeError):
        run(program, max_steps=1)


def test_runaway_growth_is_stopped():
    with pytest.raises(SiplRuntimeError) as info:
        run_text("begin X := 2; while 1 > 0 do X := X * X end")
    assert info.value.diagnostic.code == "value-too-large"
    assert str(MAX_INT_BITS) in info.value.diagnostic.message


def test_deep_nesting_is_a_diagnostic_not_a_crash():
    depth = 5000
    program = parse(f"begin {'begin ' * depth}skip{' end' * depth} end")
    with pytest.raises(SiplRuntimeError) as info:
        run(program)
    assert info.value.diagnostic.code == "too-deep"


def test_trace_of_a_small_program_is_exact():
    program = parse("begin\n X := 1;\n if X > 0 then skip else skip\nend")
    state = {}
    events = list(execute(program, state))
    assert [type(event) for event in events] == [
        BeforeStatement,
        VariableChanged,
        AfterStatement,
        BeforeStatement,
        ConditionChecked,
        BeforeStatement,
        AfterStatement,
        AfterStatement,
        Finished,
    ]
    assert events[1].name == "X" and events[1].old is None and events[1].new == 1
    assert events[4].value is True
    assert events[-1].state == {"X": 1} and events[-1].steps == 3
    assert state == {"X": 1}


def test_trace_reports_previous_value():
    events = list(execute(parse("begin X := X + 1 end"), {"X": 1}))
    change = next(event for event in events if isinstance(event, VariableChanged))
    assert (change.old, change.new) == (1, 2)


def test_trace_checks_loop_condition_once_per_iteration_and_at_exit():
    events = execute(parse("begin while X > 0 do X := X - 1 end"), {"X": 2})
    values = [event.value for event in events if isinstance(event, ConditionChecked)]
    assert values == [True, True, False]


def test_failed_trace_keeps_events_up_to_the_error():
    events = []
    with pytest.raises(SiplRuntimeError):
        for event in execute(parse("begin X := 1; Y := Q end"), {}):
            events.append(event)
    assert any(isinstance(event, VariableChanged) for event in events)
    assert not any(isinstance(event, Finished) for event in events)


def test_events_serialize_to_json():
    for event in execute(parse("begin X := 1; while X > 1 do skip end"), {}):
        data = json.loads(json.dumps(event_to_dict(event)))
        assert data["event"] == type(event).__name__
        assert data["span"]["start_line"] == 1


def test_every_stop_point_during_execution_is_known_statically():
    program = parse((EXAMPLES / "gcd.sipl").read_text(encoding="utf-8"))
    static = set(stop_point_spans(program))
    runtime = {e.span for e in execute(program, {"A": 48, "B": 18}) if is_stop_point(e)}
    assert runtime and runtime <= static


def test_stop_points_exclude_blocks_and_use_condition_span():
    program = parse("begin\n while\n  X > 0 do\n  begin\n   skip\n  end\nend")
    assert [span.start_line for span in stop_point_spans(program)] == [3, 5]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("  ", {}),
        ("X=2", {"X": 2}),
        ("X=2 Y=-10", {"X": 2, "Y": -10}),
        ("X=2, Y=3", {"X": 2, "Y": 3}),
    ],
)
def test_parse_state(text, expected):
    assert parse_state(text) == expected


@pytest.mark.parametrize("text", ["X", "X=", "=1", "X=a", "1X=1", "X=1.5", f"X={'1' * 2000}"])
def test_parse_state_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_state(text)
