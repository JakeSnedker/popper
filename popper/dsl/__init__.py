"""Strategy DSL: feature library, validator, compiler (spec/01-dsl.md).

Public surface:
    resolve_feature / compute_feature  — the only names an alpha may reference
    validate_spec                      — reject bad specs before any compute
    compile_to_backtest                — spec -> runnable callable over bars
    load_spec                          — YAML file -> StrategySpec
"""

from popper.dsl.compiler import compile_to_backtest
from popper.dsl.features import compute_feature, resolve_feature
from popper.dsl.loader import load_spec
from popper.dsl.validator import DSLValidationError, validate_spec

__all__ = [
    "compile_to_backtest",
    "compute_feature",
    "resolve_feature",
    "load_spec",
    "validate_spec",
    "DSLValidationError",
]
