"""Typed AST of SIPL. Every node knows its position in the source text."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Source range: lines and columns are 1-based, the end column is exclusive."""

    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True, slots=True)
class Number:
    value: int
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Var:
    name: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class BinOp:
    op: str  # one of + - *
    left: AExpr
    right: AExpr
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Compare:
    op: str  # one of = >
    left: AExpr
    right: AExpr
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Or:
    left: BExpr
    right: BExpr
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Not:
    operand: BExpr
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Assign:
    name: str
    value: AExpr
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Seq:
    """S1; S2; ... flattened: sequencing is associative, and this keeps long programs shallow."""

    statements: tuple[Stmt, ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class If:
    condition: BExpr
    then_branch: Stmt
    else_branch: Stmt
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class While:
    condition: BExpr
    body: Stmt
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Block:
    body: Stmt
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Skip:
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Program:
    body: Stmt
    span: SourceSpan


AExpr = Number | Var | BinOp
BExpr = Compare | Or | Not
Expr = AExpr | BExpr
Stmt = Assign | Seq | If | While | Block | Skip
Node = Expr | Stmt | Program


def to_dict(value: Any) -> Any:
    """Convert an AST node or span to plain JSON-compatible data."""
    if isinstance(value, tuple):
        return [to_dict(item) for item in value]
    if is_dataclass(value):
        data = {f.name: to_dict(getattr(value, f.name)) for f in fields(value)}
        return data if isinstance(value, SourceSpan) else {"type": type(value).__name__, **data}
    return value
