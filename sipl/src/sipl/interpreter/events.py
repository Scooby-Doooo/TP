"""Execution events: the trace model shared by the CLI and the debugger."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from sipl.ast import SourceSpan, to_dict


@dataclass(frozen=True, slots=True)
class BeforeStatement:
    span: SourceSpan
    statement: str


@dataclass(frozen=True, slots=True)
class AfterStatement:
    span: SourceSpan
    statement: str


@dataclass(frozen=True, slots=True)
class VariableChanged:
    span: SourceSpan
    name: str
    old: int | None  # None: the variable had no value before
    new: int


@dataclass(frozen=True, slots=True)
class ConditionChecked:
    span: SourceSpan
    value: bool


@dataclass(frozen=True, slots=True)
class Finished:
    span: SourceSpan
    state: dict[str, int]
    steps: int


Event = BeforeStatement | AfterStatement | VariableChanged | ConditionChecked | Finished


def event_to_dict(event: Event) -> dict[str, Any]:
    data = {f.name: getattr(event, f.name) for f in fields(event)}
    return {"event": type(event).__name__, **data, "span": to_dict(event.span)}
