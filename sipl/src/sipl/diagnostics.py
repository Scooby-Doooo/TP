"""Diagnostics shared by the parser, the interpreter and every client."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sipl.ast import SourceSpan


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    span: SourceSpan | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format(self, path: str) -> str:
        location = f"{path}:{self.span.start_line}:{self.span.start_column}" if self.span else path
        return f"{location}: помилка[{self.code}]: {self.message}"


class SiplError(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class SiplSyntaxError(SiplError):
    pass


class SiplRuntimeError(SiplError):
    pass
