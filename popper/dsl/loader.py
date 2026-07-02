"""Load a YAML-authored spec into a StrategySpec (spec/01: YAML in, Pydantic-validated)."""

from pathlib import Path

import yaml

from popper.artifacts.strategy_spec import StrategySpec


def load_spec(path: str | Path) -> StrategySpec:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return StrategySpec.model_validate(data)
