"""Feature-gap ledger helpers.

Turns useful validation failures (unknown feature/function/data context) into
roadmap memory instead of letting rejected ideas disappear.
"""

import re
from typing import Any

from popper.dsl import DSLValidationError

_UNKNOWN_FEATURE_RE = re.compile(r"unknown feature: '([^']+)'")
_UNKNOWN_NAME_RE = re.compile(r"unknown name '([^']+)'")
_TIMEFRAME_RE = re.compile(r"context\.timeframe '([^']+)'")
_UNSUPPORTED_SYMBOLS_RE = re.compile(r"unsupported symbols \[([^\]]+)\]")
_CALL_EXPR_RE = re.compile(r"function calls not allowed.*?: '([^']+)'")
_FUNC_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_BAR_COLUMNS = {"open", "high", "low", "close", "time", "tick_volume"}


def feature_gaps_from_error(err: DSLValidationError | Exception) -> list[dict[str, str]]:
    """Extract feature-gap candidates from validation text."""
    errors = getattr(err, "errors", None) or [str(err)]
    gaps: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, kind: str, detail: str) -> None:
        name = name.strip().strip('"')
        if not name or name in _BAR_COLUMNS:
            return
        key = (name.lower(), kind)
        if key in seen:
            return
        seen.add(key)
        gaps.append({"name": name, "kind": kind, "detail": detail})

    for e in errors:
        text = str(e)
        for name in _UNKNOWN_FEATURE_RE.findall(text):
            add(name, "feature", text)
        for name in _UNKNOWN_NAME_RE.findall(text):
            add(name, "dsl_name", text)
        for expr in _CALL_EXPR_RE.findall(text):
            for fn in _FUNC_RE.findall(expr):
                add(fn, "dsl_function", text)
        m = _TIMEFRAME_RE.search(text)
        if m:
            add(f"timeframe:{m.group(1)}", "data_context", text)
        m = _UNSUPPORTED_SYMBOLS_RE.search(text)
        if m:
            for raw in m.group(1).split(","):
                add(f"symbol:{raw.strip().strip(chr(39))}", "data_context", text)
    return gaps


def record_feature_gaps(mem: Any, err: DSLValidationError | Exception, *, source: str,
                        hypothesis=None, spec=None) -> list[dict[str, str]]:
    """Persist extracted gaps if the memory backend supports it."""
    gaps = feature_gaps_from_error(err)
    example = None
    if hypothesis is not None:
        example = getattr(hypothesis, "claim", None)
    for gap in gaps:
        mem.add_feature_gap(
            name=gap["name"], kind=gap["kind"], source=source,
            detail=gap["detail"], example=example,
        )
    return gaps
