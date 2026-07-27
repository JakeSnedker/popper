"""Strategy Forge orchestration for promoted Popper artifacts."""

from dataclasses import dataclass
import re

from popper.artifacts import StrategySpec
from popper.forge.mql5 import Mql5Unsupported, generate_mql5
from popper.forge.pine import PineUnsupported, generate_pine


class ForgeUnsupported(RuntimeError):
    pass


@dataclass(frozen=True)
class ForgeBundle:
    hypothesis_id: str
    spec_id: str
    files: dict[str, str]
    warnings: tuple[str, ...]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:60] or "popper_strategy"


def promoted_spec(record: dict) -> StrategySpec:
    hypothesis = record.get("hypothesis") or {}
    if hypothesis.get("status") != "promoted":
        raise ForgeUnsupported("Strategy Forge is restricted to promoted hypotheses")
    specs = {row["id"]: row for row in record.get("specs", [])}
    experiments = {row["id"]: row for row in record.get("experiments", [])}
    verdicts = sorted(
        (row for row in record.get("verdicts", []) if row.get("type") == "promote"),
        key=lambda row: row["created_at"],
        reverse=True,
    )
    for verdict in verdicts:
        experiment = experiments.get(verdict["experiment_id"])
        if experiment and experiment["spec_id"] in specs:
            return StrategySpec(**specs[experiment["spec_id"]])
    if specs:
        return StrategySpec(**next(iter(specs.values())))
    raise ForgeUnsupported("promoted hypothesis has no StrategySpec")


def forge_spec(spec: StrategySpec, targets=("pine", "mql5")) -> ForgeBundle:
    files = {}
    slug = _slug(spec.name)
    try:
        if "pine" in targets:
            files[f"{slug}.pine"] = generate_pine(spec)
        if "mql5" in targets:
            files[f"{slug}.mq5"] = generate_mql5(spec)
    except (PineUnsupported, Mql5Unsupported) as exc:
        raise ForgeUnsupported(str(exc)) from exc
    unknown = set(targets) - {"pine", "mql5"}
    if unknown:
        raise ForgeUnsupported(f"unknown Forge targets: {', '.join(sorted(unknown))}")
    warnings = (
        "Generated code must be parity-tested against Popper's Python backtest before live use.",
        "Broker session time, tick volume, spread, and fill behavior can change results.",
        "MQL5 CUSUM uses a bounded historical warmup; increase InpCusumScanBars for parity checks.",
    )
    readme = "\n".join([
        f"Popper Strategy Forge: {spec.name}",
        f"Hypothesis: {spec.hypothesis_id}",
        f"StrategySpec: {spec.id}",
        "",
        *(f"WARNING: {warning}" for warning in warnings),
        "",
        "Pine: open TradingView Pine Editor, paste the .pine file, then Add to chart.",
        "MQL5: place the .mq5 file under MQL5/Experts, compile in MetaEditor, then test in Strategy Tester.",
    ])
    files["README.txt"] = readme + "\n"
    return ForgeBundle(
        hypothesis_id=spec.hypothesis_id,
        spec_id=spec.id or "unstored",
        files=files,
        warnings=warnings,
    )


def forge_record(record: dict, targets=("pine", "mql5")) -> ForgeBundle:
    return forge_spec(promoted_spec(record), targets=targets)
