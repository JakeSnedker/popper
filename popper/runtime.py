"""Stable node, release, dataset and model attribution for research telemetry."""

import json
import platform
from functools import lru_cache

from popper import __version__
from popper.config import settings
from popper.data.bars import CANONICAL_DATASET_DIR


@lru_cache(maxsize=1)
def runtime_attribution() -> dict[str, str]:
    dataset_id = settings.dataset_id
    if not dataset_id:
        manifest_path = CANONICAL_DATASET_DIR / "dataset-manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                dataset_id = (
                    f"{manifest['dataset_version']}@{manifest['dataset_sha256']}"
                )
            except (KeyError, json.JSONDecodeError):
                dataset_id = "invalid-dataset-manifest"
    return {
        "machine_id": settings.machine_id or platform.node() or "unknown-machine",
        "release_id": settings.release_id or f"dev-{__version__}",
        "dataset_id": dataset_id or "no-dataset",
    }


def attributed_event(event: dict, operator_id: str | None = None) -> dict:
    row = {**runtime_attribution(), **event}
    payload = row.get("payload") or {}
    calls = (payload.get("llm_calls") or []) if isinstance(payload, dict) else []
    row.setdefault("model_ids", sorted({
        f"{call.get('provider')}:{call.get('model')}"
        for call in calls
        if call.get("provider") and call.get("model")
    }))
    if operator_id is not None:
        row.setdefault("operator_id", operator_id)
    return row
