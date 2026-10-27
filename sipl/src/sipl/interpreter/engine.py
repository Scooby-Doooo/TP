"""SIPL execution as a stream of events.

execute() is a generator, so the same code serves a plain run, a trace and a
step-by-step debugger: the consumer decides when to ask for the next event.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

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
from sipl.diagnostics import Diagnostic, SiplRuntimeError
from sipl.interpreter.events import (
    AfterStatement,
    BeforeStatement,
    ConditionChecked,
    Event,
    Finished,
    VariableChanged,
)

DEFAULT_MAX_STEPS = 100_000
# Stops runaway growth such as X := X * X in a loop, and keeps every result printable.
MAX_INT_BITS = 10_000

_STATE_ITEM = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(-?[0-9]{1,1000})")


def parse_state(text: str) -> dict[str, int]:
    """Parse an initial state written as "X=2 Y=10" (spaces or commas between items)."""
    state: dict[str, int] = {}
    for item in re.split(r"[\s,]+", text.strip()):
        if item:
            match = _STATE_ITEM.fullmatch(item)
            if match is None:
                raise ValueError(f"Некоректний елемент стану '{item}'; очікувалось ІМ'Я=ціле_число")
            state[match[1]] = int(match[2])
    return state


def evaluate(expr: Expr, state: dict[str, int]) -> int | bool:
    """Evaluate an expression. Both sides of ∨ are always evaluated: operators are strict."""
    match expr:
        case Number(value=value):
            return value
        case Var(name=name, span=span):
            if name not in state:
                raise SiplRuntimeError(
                    Diagnostic(
                        "undefined-variable",
                        f"Змінна '{name}' не має значення; задайте її у початковому стані "
                        "або присвойте перед використанням",
                        span,
                    )
                )
            return state[name]
        case BinOp(op=op, left=left, right=right, span=span):
            a, b = evaluate(left, state), evaluate(right, state)
            result = a + b if op == "+" else a - b if op == "-" else a * b
            if abs(result).bit_length() > MAX_INT_BITS:
                raise SiplRuntimeError(
                    Diagnostic(
                        "value-too-large",
                        f"Результат перевищує допустимий розмір ({MAX_INT_BITS} біт)",
                        span,
                    )
                )
            return result
        case Compare(op=op, left=left, right=right):
            a, b = evaluate(left, state), evaluate(right, state)
            return a == b if op == "=" else a > b
        case Or(left=left, right=right):
            a, b = evaluate(left, state), evaluate(right, state)
            return bool(a) or bool(b)
        case Not(operand=operand):
            return not evaluate(operand, state)
    raise AssertionError(f"Unknown expression: {expr!r}")


def is_stop_point(event: Event) -> bool:
    """Atomic actions a debugger pauses at, once per loop iteration for `while`."""
    if isinstance(event, BeforeStatement):
        return event.statement in ("assign", "skip")
    return isinstance(event, ConditionChecked)


def stop_point_spans(node: Stmt | Program) -> Iterator[SourceSpan]:
    """Source ranges of all stop points, in source order. Breakpoints bind to these."""
    match node:
        case Program(body=body) | Block(body=body):
            yield from stop_point_spans(body)
        case Seq(statements=statements):
            for statement in statements:
                yield from stop_point_spans(statement)
        case If(condition=condition, then_branch=then_branch, else_branch=else_branch):
            yield condition.span
            yield from stop_point_spans(then_branch)
            yield from stop_point_spans(else_branch)
        case While(condition=condition, body=body):
            yield condition.span
            yield from stop_point_spans(body)
        case Assign(span=span) | Skip(span=span):
            yield span


class _Machine:
    def __init__(self, state: dict[str, int], max_steps: int) -> None:
        self.state = state
        self.max_steps = max_steps
        self.steps = 0

    def tick(self, span: SourceSpan) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise SiplRuntimeError(
                Diagnostic(
                    "step-limit",
                    f"Перевищено ліміт кроків ({self.max_steps}); можливо, цикл нескінченний",
                    span,
                )
            )

    def check(self, condition: BExpr) -> ConditionChecked:
        self.tick(condition.span)
        return ConditionChecked(condition.span, bool(evaluate(condition, self.state)))

    def run(self, node: Stmt) -> Iterator[Event]:
        if isinstance(node, Seq):
            for statement in node.statements:
                yield from self.run(statement)
            return

        kind = type(node).__name__.lower()
        yield BeforeStatement(node.span, kind)
        match node:
            case Assign(name=name, value=value, span=span):
                self.tick(span)
                new = int(evaluate(value, self.state))
                old = self.state.get(name)
                self.state[name] = new
                yield VariableChanged(span, name, old, new)
            case If(condition=condition, then_branch=then_branch, else_branch=else_branch):
                check = self.check(condition)
                yield check
                yield from self.run(then_branch if check.value else else_branch)
            case While(condition=condition, body=body):
                while True:
                    check = self.check(condition)
                    yield check
                    if not check.value:
                        break
                    yield from self.run(body)
            case Block(body=body):
                yield from self.run(body)
            case Skip(span=span):
                self.tick(span)
        yield AfterStatement(node.span, kind)


def execute(
    program: Program, state: dict[str, int], *, max_steps: int = DEFAULT_MAX_STEPS
) -> Iterator[Event]:
    """Yield execution events, updating `state` in place. Raises SiplRuntimeError on failure."""
    machine = _Machine(state, max_steps)
    try:
        yield from machine.run(program.body)
    except RecursionError:
        raise SiplRuntimeError(
            Diagnostic("too-deep", "Програма має занадто глибоку вкладеність", program.span)
        ) from None
    yield Finished(program.span, dict(state), machine.steps)


def run(
    program: Program, state: dict[str, int] | None = None, *, max_steps: int = DEFAULT_MAX_STEPS
) -> dict[str, int]:
    """Run a program to completion and return its final state."""
    final = dict(state or {})
    for _ in execute(program, final, max_steps=max_steps):
        pass
    return final
