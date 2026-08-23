"""Translate Popper's bounded signal AST to Pine or MQL boolean expressions."""

import ast
import re

from popper.dsl.expr import parse_signal
from popper.dsl.features import CISD_RUN, SWING_LOOKBACK


class ForgeExpressionError(ValueError):
    pass


# The SMC name families: one tunable knob carried as a suffix, exactly like
# ema_<n>. Mirrors _SMC_BASE / _SMC_RE in popper/dsl/features/__init__.py --
# both emitters read this, so the bound and the defaults live in one place.
_SMC_BASES = ("bos", "mss", "equilibrium", "ob_bull_mid", "ob_bear_mid", "cisd")
_SMC = re.compile(rf"^({'|'.join(_SMC_BASES)})_(\d+)$")


def smc_knob(feature: str) -> tuple[str, int] | None:
    """(base, knob) for a structural feature, or None if it is not one.

    The bare name carries the module default -- `bos` is `bos_2` and `cisd` is
    `cisd_1` -- and the suffix bound matches resolve_feature's, so a name the
    DSL would reject never reaches an emitter as a renderable family member.
    """
    if feature in _SMC_BASES:
        return feature, CISD_RUN if feature == "cisd" else SWING_LOOKBACK
    match = _SMC.match(feature)
    if not match or not 1 <= int(match.group(2)) <= 100:
        return None
    return match.group(1), int(match.group(2))


def input_name(name: str, target: str) -> str:
    if target == "pine":
        return re.sub(r"\W+", "_", name)
    parts = re.split(r"[^A-Za-z0-9]+", name)
    return "Inp" + "".join(part[:1].upper() + part[1:] for part in parts if part)


def anchor_level(spec, value: str) -> str:
    """Resolve a stop/target/entry anchor to a literal level-feature name.

    An anchor is either the name itself or a `p[...]` ref into param_space (a
    swept categorical). An emitter renders ONE strategy, so it takes the same
    grid point the compiler defaults to -- the middle, as
    CompiledStrategy.default_params does. Sweeping the anchor needs one forge
    run per grid point.
    """
    match = re.match(r"^p\[(\w+)\]$", value)
    if not match:
        return value
    grid = spec.param_space[match.group(1)]
    return str(grid[len(grid) // 2])


def compile_expression(text: str, target: str) -> str:
    if target not in {"pine", "mql5"}:
        raise ValueError(f"unknown forge target {target!r}")
    bool_ops = {ast.And: "and" if target == "pine" else "&&",
                ast.Or: "or" if target == "pine" else "||"}
    cmp_ops = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=",
               ast.Eq: "==", ast.NotEq: "!="}
    bin_ops = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}

    def emit(node) -> str:
        if isinstance(node, ast.Expression):
            return emit(node.body)
        if isinstance(node, ast.BoolOp):
            op = bool_ops[type(node.op)]
            return "(" + f" {op} ".join(emit(value) for value in node.values) + ")"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"({'not ' if target == 'pine' else '!'}{emit(node.operand)})"
            if isinstance(node.op, ast.USub):
                return f"(-{emit(node.operand)})"
        if isinstance(node, ast.BinOp):
            return f"({emit(node.left)} {bin_ops[type(node.op)]} {emit(node.right)})"
        if isinstance(node, ast.Compare):
            parts = []
            left = node.left
            for op, right in zip(node.ops, node.comparators):
                parts.append(f"({emit(left)} {cmp_ops[type(op)]} {emit(right)})")
                left = right
            joiner = " and " if target == "pine" else " && "
            return "(" + joiner.join(parts) + ")"
        if isinstance(node, ast.Subscript):
            return input_name(node.slice.id, target)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return repr(node.value)
        raise ForgeExpressionError(f"unsupported signal node: {type(node).__name__}")

    return emit(parse_signal(text))
