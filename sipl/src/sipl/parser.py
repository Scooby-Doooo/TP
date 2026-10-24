"""Source text -> typed AST. Lark stays an implementation detail of this module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lark import Lark, Token
from lark.exceptions import UnexpectedCharacters, UnexpectedInput, UnexpectedToken, VisitError
from lark.tree import Meta
from lark.visitors import Transformer_NonRecursive, v_args

from sipl.ast import (
    Assign,
    BExpr,
    BinOp,
    Block,
    Compare,
    Expr,
    If,
    Not,
    Number,
    Or,
    Program,
    Seq,
    Skip,
    SourceSpan,
    Stmt,
    Var,
    While,
)
from sipl.diagnostics import Diagnostic, SiplSyntaxError

_PARSER = Lark(
    (Path(__file__).with_name("grammar") / "sipl.lark").read_text(encoding="utf-8"),
    parser="lalr",
    start=["program", "aexp", "bexp"],
    propagate_positions=True,
)

_TERMINALS = {
    "_BEGIN": "begin",
    "_END": "end",
    "_IF": "if",
    "_THEN": "then",
    "_ELSE": "else",
    "_WHILE": "while",
    "_DO": "do",
    "_SKIP": "skip",
    "_OR": "∨",
    "_OR_WORD": "or",
    "_NOT": "¬",
    "_NOT_WORD": "not",
    "_ASSIGN": ":=",
    "_SEMI": ";",
    "_LPAR": "(",
    "_RPAR": ")",
    "PLUS": "+",
    "MINUS": "-",
    "TIMES": "*",
    "EQ": "=",
    "GT": ">",
    "NUMBER": "число",
    "NAME": "змінна",
    "$END": "кінець тексту",
}


def _token_span(token: Token) -> SourceSpan:
    assert token.line is not None and token.column is not None
    assert token.end_line is not None and token.end_column is not None
    return SourceSpan(token.line, token.column, token.end_line, token.end_column)


def _meta_span(meta: Meta) -> SourceSpan:
    return SourceSpan(meta.line, meta.column, meta.end_line, meta.end_column)


def _number(token: Token) -> int:
    try:
        return int(token)
    except ValueError:  # Python refuses to convert integers beyond its digit limit
        raise SiplSyntaxError(
            Diagnostic("number-too-long", "Числовий літерал занадто довгий", _token_span(token))
        ) from None


@v_args(meta=True)
class _ToAst(Transformer_NonRecursive[Token, Any]):
    def program(self, meta: Meta, children: list[Stmt]) -> Program:
        return Program(children[0], _meta_span(meta))

    def seq(self, meta: Meta, children: list[Stmt]) -> Seq:
        return Seq(tuple(children), _meta_span(meta))

    def assign(self, meta: Meta, children: list[Any]) -> Assign:
        return Assign(str(children[0]), children[1], _meta_span(meta))

    def if_(self, meta: Meta, children: list[Any]) -> If:
        return If(children[0], children[1], children[2], _meta_span(meta))

    def while_(self, meta: Meta, children: list[Any]) -> While:
        return While(children[0], children[1], _meta_span(meta))

    def block(self, meta: Meta, children: list[Stmt]) -> Block:
        return Block(children[0], _meta_span(meta))

    def skip(self, meta: Meta, children: list[Any]) -> Skip:
        return Skip(_meta_span(meta))

    def number(self, meta: Meta, children: list[Token]) -> Number:
        return Number(_number(children[0]), _meta_span(meta))

    def var(self, meta: Meta, children: list[Token]) -> Var:
        return Var(str(children[0]), _meta_span(meta))

    def binary(self, meta: Meta, children: list[Any]) -> BinOp:
        return BinOp(str(children[1]), children[0], children[2], _meta_span(meta))

    def compare(self, meta: Meta, children: list[Any]) -> Compare:
        return Compare(str(children[1]), children[0], children[2], _meta_span(meta))

    def or_(self, meta: Meta, children: list[BExpr]) -> Or:
        return Or(children[0], children[1], _meta_span(meta))

    def not_(self, meta: Meta, children: list[BExpr]) -> Not:
        return Not(children[0], _meta_span(meta))


def _end_span(text: str) -> SourceSpan:
    line = text.count("\n") + 1
    column = len(text) - (text.rfind("\n") + 1) + 1
    return SourceSpan(line, column, line, column)


def _expected(terminals: set[str]) -> str:
    return ", ".join(sorted({f"'{_TERMINALS.get(name, name)}'" for name in terminals}))


def _diagnostic(error: UnexpectedInput, text: str) -> Diagnostic:
    if isinstance(error, UnexpectedToken):
        token = error.token
        if token.type == "$END":
            span, found = _end_span(text), "кінець тексту"
        else:
            span, found = _token_span(token), f"'{token}'"
        expected = set(error.accepts or error.expected)
    elif isinstance(error, UnexpectedCharacters):
        line, column = error.line, error.column
        assert error.pos_in_stream is not None
        span = SourceSpan(line, column, line, column + 1)
        found, expected = f"символ '{text[error.pos_in_stream]}'", set(error.allowed)
    else:  # any other Lark input error carries no reliable position
        return Diagnostic("syntax", str(error), _end_span(text))
    return Diagnostic("syntax", f"Неочікуваний {found}; очікувалось: {_expected(expected)}", span)


def _parse(text: str, start: str) -> Any:
    try:
        return _ToAst().transform(_PARSER.parse(text, start=start))
    except UnexpectedInput as error:
        raise SiplSyntaxError(_diagnostic(error, text)) from None
    except VisitError as error:
        if isinstance(error.orig_exc, SiplSyntaxError):
            raise error.orig_exc from None
        raise


def parse(text: str) -> Program:
    """Parse a whole program. Raises SiplSyntaxError with a positioned diagnostic."""
    result: Program = _parse(text, "program")
    return result


def parse_expression(text: str) -> Expr:
    """Parse a standalone condition or arithmetic expression (used by debugger evaluate)."""
    try:
        condition: Expr = _parse(text, "bexp")
    except SiplSyntaxError as condition_error:
        try:
            expression: Expr = _parse(text, "aexp")
        except SiplSyntaxError:
            raise condition_error from None
        return expression
    return condition
