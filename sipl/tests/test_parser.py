from pathlib import Path

import pytest

from sipl.ast import (
    Assign,
    BinOp,
    Block,
    Compare,
    If,
    Not,
    Number,
    Or,
    Program,
    Seq,
    Skip,
    SourceSpan,
    Var,
    While,
    to_dict,
)
from sipl.diagnostics import SiplSyntaxError
from sipl.parser import parse, parse_expression

EXAMPLES = Path(__file__).parent.parent / "examples"

VALID = [
    "begin skip end",
    "begin X := 1 end",
    "begin X := -1 end",
    "begin X := 1; Y := 2; Z := 3 end",
    "begin begin skip end end",
    "begin if X > 0 then skip else skip end",
    "begin if X = 0 then begin A := 1; B := 2 end else skip end",
    "begin while X > 0 do X := X - 1 end",
    "begin while ¬ X = 0 do X := X - 1 end",
    "begin while not X = 0 do X := X - 1 end",
    "begin if X > 0 ∨ X = 0 then skip else skip end",
    "begin if X > 0 or X = 0 then skip else skip end",
    "begin if (X > 0) then skip else skip end",
    "begin if (X + 1) * 2 > 0 then skip else skip end",
    "begin if ((X) = (Y)) then skip else skip end",
    "begin X := (1 + 2) * (3 - 4) end",
    "begin order := 1; beginning := notx end",
    "\n\n begin\tskip\r\n end \n",
]

INVALID = [
    "",
    "skip",
    "X := 1",
    "begin end",
    "begin skip",
    "begin skip end end",
    "begin X := 1; end",
    "begin ; X := 1 end",
    "begin X = 1 end",
    "begin X := end",
    "begin X := 1 + end",
    "begin X := - 1 end",
    "begin X := (1 end",
    "begin X := 1) end",
    "begin 1 := X end",
    "begin begin := 1 end",
    "begin then := 1 end",
    "begin X := end end",
    "begin X := skip end",
    "begin or := 1 end",
    "begin not := 1 end",
    "begin X := Y do end",
    "begin if X > 0 then skip end",
    "begin if X then skip else skip end",
    "begin if X > 0 skip else skip end",
    "begin while X do skip end",
    "begin while X > 0 skip end",
    "begin while X > 0 do A := 1; B := 2 else skip end",
    "begin if X > 0 then A := 1; B := 2 else skip end",
    "begin if X < 0 then skip else skip end",
    "begin X := 1 > 0 end",
    "begin X := 1 # 2 end",
    "begin Змінна := 1 end",
]


@pytest.mark.parametrize("text", VALID)
def test_valid_programs(text):
    assert isinstance(parse(text), Program)


@pytest.mark.parametrize("text", INVALID)
def test_invalid_programs_get_positioned_diagnostic(text):
    with pytest.raises(SiplSyntaxError) as info:
        parse(text)
    diagnostic = info.value.diagnostic
    assert diagnostic.code == "syntax"
    assert diagnostic.span is not None
    assert diagnostic.span.start_line >= 1 and diagnostic.span.start_column >= 1
    assert "очікувалось" in diagnostic.message


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.sipl")), ids=lambda p: p.name)
def test_example_files_parse(path):
    parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("invalid/*.sipl")), ids=lambda p: p.name)
def test_invalid_example_files_fail(path):
    with pytest.raises(SiplSyntaxError):
        parse(path.read_text(encoding="utf-8"))


def test_ast_uses_own_nodes_with_all_statement_kinds():
    program = parse(
        "begin X := 1; if X > 0 then skip else begin Y := 2 end; while ¬ X = 0 ∨ X > 5 do skip end"
    )
    assert isinstance(program.body, Seq)
    assign, branch, loop = program.body.statements
    assert isinstance(assign, Assign) and assign.name == "X" and isinstance(assign.value, Number)
    assert isinstance(branch, If) and isinstance(branch.condition, Compare)
    assert isinstance(branch.then_branch, Skip) and isinstance(branch.else_branch, Block)
    assert isinstance(loop, While) and isinstance(loop.condition, Or)
    assert isinstance(loop.condition.left, Not)


def test_arithmetic_precedence_and_left_associativity():
    value = parse("begin X := 1 - 2 - 3 * 4 end").body.value
    assert isinstance(value, BinOp) and value.op == "-"
    assert isinstance(value.left, BinOp) and value.left.op == "-"
    assert isinstance(value.right, BinOp) and value.right.op == "*"


def test_parentheses_override_precedence():
    value = parse("begin X := (1 + 2) * 3 end").body.value
    assert value.op == "*" and value.left.op == "+"


def test_loop_binds_tighter_than_sequence():
    body = parse("begin while X > 0 do X := X - 1; Y := 2 end").body
    assert isinstance(body, Seq) and isinstance(body.statements[0], While)
    assert isinstance(body.statements[0].body, Assign)


@pytest.mark.parametrize(
    ("text", "left", "right"),
    [("X-1", "X", 1), ("X -1", "X", 1), ("X - -1", "X", -1)],
)
def test_minus_is_contextual(text, left, right):
    value = parse(f"begin Y := {text} end").body.value
    assert value.op == "-" and value.left.name == left and value.right.value == right


def test_negative_number_literal():
    assert parse("begin Y := -5 end").body.value.value == -5


def test_spans_point_at_source():
    program = parse("begin\n  R := 1;\n  while Y > 0 do\n    skip\nend")
    assign, loop = program.body.statements
    assert program.span == SourceSpan(1, 1, 5, 4)
    assert assign.span == SourceSpan(2, 3, 2, 9)
    assert assign.value.span == SourceSpan(2, 8, 2, 9)
    assert loop.span == SourceSpan(3, 3, 4, 9)
    assert loop.condition.span == SourceSpan(3, 9, 3, 14)
    assert loop.body.span == SourceSpan(4, 5, 4, 9)


def test_block_span_includes_keywords():
    block = parse("begin begin skip end end").body
    assert isinstance(block, Block) and block.span == SourceSpan(1, 7, 1, 21)


def test_diagnostic_position_and_text_for_unexpected_token():
    with pytest.raises(SiplSyntaxError) as info:
        parse("begin\n  R := 1;\nend")
    diagnostic = info.value.diagnostic
    assert diagnostic.span == SourceSpan(3, 1, 3, 4)
    assert "'end'" in diagnostic.message and "'змінна'" in diagnostic.message


def test_diagnostic_position_for_end_of_text():
    with pytest.raises(SiplSyntaxError) as info:
        parse("begin\n  skip")
    assert info.value.diagnostic.span == SourceSpan(2, 7, 2, 7)
    assert "кінець тексту" in info.value.diagnostic.message


def test_diagnostic_position_for_unknown_character():
    with pytest.raises(SiplSyntaxError) as info:
        parse("begin\n  X := 1 # 2\nend")
    assert info.value.diagnostic.span == SourceSpan(2, 10, 2, 11)
    assert "'#'" in info.value.diagnostic.message


def test_too_long_number_literal_is_a_diagnostic():
    with pytest.raises(SiplSyntaxError) as info:
        parse(f"begin X := {'9' * 5000} end")
    assert info.value.diagnostic.code == "number-too-long"
    assert info.value.diagnostic.span is not None


def test_deep_nesting_does_not_crash_the_parser():
    depth = 5000
    assert parse(f"begin X := {'(' * depth}1{')' * depth} end")


def test_to_dict_is_plain_data():
    data = to_dict(parse("begin X := 1; skip end"))
    assert data["type"] == "Program"
    assert data["body"]["type"] == "Seq"
    assert data["body"]["statements"][0] == {
        "type": "Assign",
        "name": "X",
        "value": {
            "type": "Number",
            "value": 1,
            "span": {"start_line": 1, "start_column": 12, "end_line": 1, "end_column": 13},
        },
        "span": {"start_line": 1, "start_column": 7, "end_line": 1, "end_column": 13},
    }


def test_parse_expression_accepts_both_kinds():
    assert isinstance(parse_expression("X + 1"), BinOp)
    assert isinstance(parse_expression("X"), Var)
    assert isinstance(parse_expression("X + 1 > 2"), Compare)
    with pytest.raises(SiplSyntaxError):
        parse_expression("X +")
