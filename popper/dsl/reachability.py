"""Reject signals that can never be true.

Roughly a third of the corpus died at the first gate with "signal fires only
0x" -- not because the idea was bad, but because the condition was impossible:
`trend_regime < 0` when trend_regime is 0 or 1, `bb_line < 0.2` when bb_line is
a price near 40,000, `session_asia AND session_london` when sessions partition
the day.

This module answers one narrow question: is there ANY world in which this
comparison is true? It only reports a problem when the answer is provably no,
so a false positive here would be a bug, not a judgement call. Anything
uncertain is left alone for the empirical check to catch downstream.
"""

from __future__ import annotations

import ast

from popper.dsl.expr import BAR_COLS, parse_signal
from popper.dsl.features.domains import (
    DISTANCE_LIKE,
    MUTUALLY_EXCLUSIVE,
    PERCENT,
    POINTS,
    PRICE,
    PRICE_LIKE,
    RATIO,
    UNIT,
    domain_for,
)

# Kinds whose stated bound is a real limit rather than a generous guess. Only
# these may be used to prove a comparison impossible -- PRICE/POINTS ceilings
# are deliberately loose, so they are never used as an upper bound.
_TIGHT_HI = {UNIT, PERCENT}
_TIGHT_LO = {UNIT, PERCENT, POINTS, RATIO, PRICE}

# A bar column is a price. Comparing one to a small literal is the same
# category error as comparing bb_line to 0.2.
_PRICE_COLS = {"open", "high", "low", "close"}
_PRICE_FLOOR = 1_000.0   # below this, a number cannot be a US30 price


def _grid_values(node: ast.AST, param_space: dict) -> list[float] | None:
    """The concrete numbers a comparison operand can take, or None if unknown."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return [float(node.value)]
    # -p[x] and bare negative literals
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _grid_values(node.operand, param_space)
        return [-v for v in inner] if inner else None
    # p[name]
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "p":
        key = node.slice
        name = key.id if isinstance(key, ast.Name) else getattr(key, "value", None)
        grid = param_space.get(name)
        if isinstance(grid, list) and grid:
            try:
                return [float(v) for v in grid]
            except (TypeError, ValueError):
                return None
    return None


def _feature_name(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _satisfiable(values: tuple[float, ...], op: ast.cmpop, targets: list[float]) -> bool:
    """Can any (value, target) pair make this comparison true?"""
    tests = {
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
    }
    fn = tests.get(type(op))
    if fn is None:
        return True
    return any(fn(v, t) for v in values for t in targets)


def _interval_satisfiable(domain, op: ast.cmpop, targets: list[float]) -> bool | None:
    """Can any value in the domain's interval satisfy this? None = cannot tell."""
    lo = domain.lo if domain.kind in _TIGHT_LO else None
    hi = domain.hi if domain.kind in _TIGHT_HI else None
    if lo is None and hi is None:
        return None
    checks = {
        ast.Gt:  lambda t: hi is None or hi > t,
        ast.GtE: lambda t: hi is None or hi >= t,
        ast.Lt:  lambda t: lo is None or lo < t,
        ast.LtE: lambda t: lo is None or lo <= t,
        ast.Eq:  lambda t: (lo is None or t >= lo) and (hi is None or t <= hi),
    }
    fn = checks.get(type(op))
    if fn is None:
        return None
    return any(fn(t) for t in targets)


def _scale_of(name: str) -> str | None:
    """'price' / 'distance' / None for a name appearing in a comparison."""
    if name in _PRICE_COLS:
        return "price"
    domain = domain_for(name)
    if domain is None:
        return None
    if domain.kind in PRICE_LIKE:
        return "price"
    if domain.kind in DISTANCE_LIKE:
        return "distance"
    return None


def _compare_errors(node: ast.Compare, param_space: dict, label: str) -> list[str]:
    errors: list[str] = []
    operands = [node.left, *node.comparators]
    for op, left, right in zip(node.ops, operands, operands[1:]):
        for feature_node, other, flipped in ((left, right, False), (right, left, True)):
            name = _feature_name(feature_node)
            if name is None or name in BAR_COLS:
                continue
            domain = domain_for(name)
            if domain is None:
                continue

            # 1. an exact value set that no target can satisfy
            targets = _grid_values(other, param_space)
            if domain.values and targets is not None and not flipped:
                if not _satisfiable(domain.values, op, targets):
                    allowed = "{" + ", ".join(str(int(v)) for v in domain.values) + "}"
                    errors.append(
                        f"{label}: `{name}` only ever takes {allowed} ({domain.note}), so this "
                        f"comparison can never be true"
                    )
                    continue

            # 2. a bounded feature whose whole range sits on one side
            if targets is not None and not flipped and not domain.values:
                verdict = _interval_satisfiable(domain, op, targets)
                if verdict is False:
                    span = f"{domain.lo:g}..{domain.hi:g}" if domain.kind in _TIGHT_HI else f">= {domain.lo:g}"
                    errors.append(
                        f"{label}: `{name}` is bounded to {span} ({domain.note}), so comparing it "
                        f"to {targets if len(targets) < 5 else 'that grid'} can never be true"
                    )
                    continue

        # 4. price compared against a distance -- but ONLY in the direction that
        # makes it impossible. `atr_14 > ema_50` (distance above price) can never
        # be true; `close > opening_range` (price above distance) is always true,
        # which is a useless condition but NOT a dead one, and specs relying on it
        # do fire. Only the impossible direction belongs in a reachability check.
        lname, rname = _feature_name(left), _feature_name(right)
        if lname and rname:
            lscale, rscale = _scale_of(lname), _scale_of(rname)
            if {lscale, rscale} == {"price", "distance"}:
                impossible = (
                    (lscale == "distance" and isinstance(op, (ast.Gt, ast.GtE)))
                    or (lscale == "price" and isinstance(op, (ast.Lt, ast.LtE)))
                )
                if impossible:
                    errors.append(
                        f"{label}: `{lname}` is a {lscale} and `{rname}` is a {rscale} -- on US30 a "
                        f"point distance is never above a price level, so this can never be true"
                    )
    return errors


def _forced_true(name: str, op: ast.cmpop, targets: list[float] | None) -> bool:
    """Does `name <op> target` force `name` to be 1?

    `session_london == 0` requires it to be FALSE; `session_asia < opening_range`
    requires nothing at all. Only a comparison satisfiable by 1 and by nothing
    else pins the flag on.
    """
    domain = domain_for(name)
    if domain is None or not domain.values or targets is None:
        return False
    satisfying = {v for v in domain.values if _satisfiable((v,), op, targets)}
    return satisfying == {1.0}


def _required_true(node: ast.AST, param_space: dict) -> set[str]:
    """Names that must be TRUE for `node` to be true.

    Descends through AND, where every branch is required, but never through OR,
    where a branch is only an alternative -- `(session_london OR session_ny)` is
    a perfectly good condition and must not read as a contradiction.
    """
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            out: set[str] = set()
            for value in node.values:
                out |= _required_true(value, param_space)
            return out
        return set()                       # OR: nothing individually required
    if isinstance(node, ast.Name):
        return {node.id}                   # a bare flag, used truthily
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        out = set()
        for op, left, right in zip(node.ops, operands, operands[1:]):
            name = _feature_name(left)
            if name and _forced_true(name, op, _grid_values(right, param_space)):
                out.add(name)
        return out
    return set()


def _exclusive_errors(tree: ast.AST, param_space: dict, label: str) -> list[str]:
    """Sessions partition the day; requiring two at once can never be true."""
    errors = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And)):
            continue
        names = _required_true(node, param_space)
        for group in MUTUALLY_EXCLUSIVE:
            clash = sorted(names & set(group))
            if len(clash) > 1:
                errors.append(
                    f"{label}: {' and '.join(clash)} are mutually exclusive -- they can never "
                    f"be true on the same bar, so this AND never fires"
                )
    return errors


def reachability_errors(text: str, param_space: dict, label: str = "signal") -> list[str]:
    """Every reason this signal can never fire. Empty means 'not provably dead'."""
    if not text or not text.strip():
        return []
    try:
        tree = parse_signal(text)
    except SyntaxError:
        return []          # the grammar check reports malformed input properly
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            errors += _compare_errors(node, param_space or {}, label)
    errors += _exclusive_errors(tree, param_space or {}, label)
    # a single condition can trip several rules; keep the first of each wording
    seen, unique = set(), []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique
