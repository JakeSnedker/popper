"""Signal expression grammar: parse + static check + vectorised eval.

Bounded, composable primitives — NOT program synthesis (spec/01). An
expression may use: declared feature names, bar columns (open/high/low/
close/volume), numbers, p[name] param refs, + - * /, comparisons,
AND/OR/NOT, parentheses. Function calls, attributes, and any other Python
construct are rejected — this structurally prevents look-ahead (there is
no way to express shift(-n)/lead/future indexing).
"""

import ast
import operator
import re
from functools import reduce

import numpy as np
import pandas as pd

BAR_COLS = {"open", "high", "low", "close", "volume"}

_ALLOWED_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_ALLOWED_CMP = (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq)


def parse_signal(text: str) -> ast.Expression:
    """DSL text -> Python AST. Raises SyntaxError on malformed input."""
    src = re.sub(r"\bAND\b", " and ", text)
    src = re.sub(r"\bOR\b", " or ", src)
    src = re.sub(r"\bNOT\b", " not ", src)
    return ast.parse(src, mode="eval")


def check_signal(text: str, features: list[str]) -> tuple[set[str], list[str]]:
    """Static check. Returns (param names referenced, errors)."""
    errors: list[str] = []
    params: set[str] = set()
    try:
        tree = parse_signal(text)
    except SyntaxError as e:
        return params, [f"malformed signal {text!r}: {e.msg}"]

    allowed_names = set(features) | BAR_COLS
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            errors.append(f"function calls not allowed in signal (look-ahead guard): {text!r}")
        elif isinstance(node, ast.Attribute):
            errors.append(f"attribute access not allowed in signal: {text!r}")
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "p" and isinstance(node.slice, ast.Name):
                params.add(node.slice.id)
            else:
                errors.append(f"only p[name] subscripts allowed in signal: {text!r}")
        elif isinstance(node, ast.Name):
            if node.id == "p":
                continue  # validated via its Subscript parent
            if node.id not in allowed_names:
                # the slice Name inside p[...] also walks here; skip known params
                if node.id not in params:
                    errors.append(f"unknown name {node.id!r} in signal (not a declared feature or bar column)")
        elif isinstance(node, ast.BinOp) and not isinstance(node.op, _ALLOWED_OPS):
            errors.append(f"operator not allowed in signal: {type(node.op).__name__}")
        elif isinstance(node, ast.Compare) and not all(isinstance(op, _ALLOWED_CMP) for op in node.ops):
            errors.append(f"comparison not allowed in signal: {text!r}")
        elif isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            errors.append(f"only numeric constants allowed in signal: {node.value!r}")
        elif not isinstance(
            node,
            (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
             ast.BinOp, ast.Compare, ast.Name, ast.Constant, ast.Subscript, ast.Load,
             *_ALLOWED_OPS, *_ALLOWED_CMP),
        ):
            errors.append(f"construct not allowed in signal: {type(node).__name__}")
    return params, errors


def _as_bool(x):
    if isinstance(x, pd.Series):
        return x if x.dtype == bool else x.fillna(0) != 0
    return bool(x)


def eval_signal(text: str, env: dict[str, pd.Series], params: dict[str, float]) -> pd.Series:
    """Evaluate a (pre-validated) signal expression to a boolean Series.

    NaN feature values compare False — a signal never fires on unformed features.
    """
    tree = parse_signal(text)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BoolOp):
            op = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            return reduce(op, (_as_bool(ev(v)) for v in node.values))
        if isinstance(node, ast.UnaryOp):
            return ~_as_bool(ev(node.operand)) if isinstance(node.op, ast.Not) else -ev(node.operand)
        if isinstance(node, ast.Compare):
            left, result = ev(node.left), None
            ops = {ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt,
                   ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                part = ops[type(op)](left, right)
                result = part if result is None else _as_bool(result) & _as_bool(part)
                left = right
            return result
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right,
                    ast.Mult: lambda: left * right, ast.Div: lambda: left / right}[type(node.op)]()
        if isinstance(node, ast.Subscript):  # p[name]
            return params[node.slice.id]
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        raise ValueError(f"unexpected node in validated signal: {type(node).__name__}")

    result = _as_bool(ev(tree))
    if not isinstance(result, pd.Series):
        raise ValueError(f"signal must reference at least one series: {text!r}")
    return result.fillna(False) if result.dtype != bool else result
