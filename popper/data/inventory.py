"""What market data does THIS machine actually have?

Onboarding ships bars (~37 MB) because that is all research reads today. Tick
data is ~1.3 GB and arrives separately — a shared drive, a disk, whenever it
arrives — so a node must be able to answer "what have I got, and what is
missing" at any point, not only at install time.

Nothing here writes or downloads. It reports, and `locate` finds a dataset the
operator has already put somewhere, so the answer to "where did I put it" is a
command rather than a support conversation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BAR_TIMEFRAMES = ("M1", "M5", "M15")
# Where an operator plausibly drops an extracted archive. Checked in order; the
# configured dataset_dir always wins.
SEARCH_HINTS = (
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path("C:/Popper"),
    Path("D:/"),
)


def _tree_size(path: Path) -> tuple[int, int]:
    """(file_count, total_bytes) under `path`, or (0, 0) if it is absent."""
    if not path.is_dir():
        return 0, 0
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def inspect(dataset_dir: str | Path) -> dict[str, Any]:
    """Describe one dataset directory without validating its hash.

    Deliberately cheap and non-fatal: a node with partial data must still be
    able to say what it has. `popper_node.py preflight` remains the authority on
    whether a dataset is the canonical one.
    """
    root = Path(dataset_dir)
    manifest = root / "dataset-manifest.json"
    bars: dict[str, Any] = {}
    for timeframe in BAR_TIMEFRAMES:
        count, size = _tree_size(root / "bars" / timeframe)
        bars[timeframe] = {"files": count, "bytes": size, "present": count > 0}
    tick_files, tick_bytes = _tree_size(root / "ticks")
    bar_bytes = sum(item["bytes"] for item in bars.values())
    return {
        "path": str(root),
        "exists": root.is_dir(),
        "manifest": manifest.is_file(),
        "bars": bars,
        "bars_complete": all(item["present"] for item in bars.values()),
        "bars_bytes": bar_bytes,
        "ticks": {"files": tick_files, "bytes": tick_bytes, "present": tick_files > 0},
        # Research reads bars only today, so a bars-only node is fully usable and
        # should not be reported as broken for missing ticks.
        "research_ready": all(item["present"] for item in bars.values()) and manifest.is_file(),
    }


def locate(extra: list[str | Path] | None = None, max_depth: int = 3) -> list[dict[str, Any]]:
    """Find dataset-looking directories the operator has already downloaded.

    A dataset is anything holding a dataset-manifest.json, or a bars/ directory
    with at least one canonical timeframe — so a half-extracted archive is still
    found and reported rather than silently missed.
    """
    seen: set[Path] = set()
    found: list[dict[str, Any]] = []
    roots = [Path(p) for p in (extra or [])] + list(SEARCH_HINTS)
    for root in roots:
        if not root.is_dir():
            continue
        candidates = [root, *(p for p in _shallow_dirs(root, max_depth))]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if (candidate / "dataset-manifest.json").is_file() or any(
                (candidate / "bars" / tf).is_dir() for tf in BAR_TIMEFRAMES
            ):
                found.append(inspect(candidate))
    return found


def _shallow_dirs(root: Path, max_depth: int):
    """Directories under `root` to `max_depth`, skipping anything unreadable.

    Bounded on purpose: an unbounded walk of C:\\ or a home directory is how a
    "quick check" turns into a five-minute hang on an operator's laptop.
    """
    frontier = [(root, 0)]
    while frontier:
        current, depth = frontier.pop()
        if depth >= max_depth:
            continue
        try:
            children = [p for p in current.iterdir() if p.is_dir()]
        except (OSError, PermissionError):
            continue
        for child in children:
            yield child
            frontier.append((child, depth + 1))


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def demo() -> None:
    """Self-check against a synthetic tree: presence, sizes, and readiness."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "ftmo-us30-v1"
        for timeframe in BAR_TIMEFRAMES:
            directory = root / "bars" / timeframe
            directory.mkdir(parents=True)
            (directory / "part.parquet").write_bytes(b"x" * 100)
        report = inspect(root)
        assert report["bars_complete"] is True, report
        assert report["ticks"]["present"] is False
        # Bars without a manifest is not yet research-ready: preflight needs it.
        assert report["research_ready"] is False, report

        (root / "dataset-manifest.json").write_text(json.dumps({"dataset_version": "x"}))
        assert inspect(root)["research_ready"] is True

        (root / "ticks").mkdir()
        (root / "ticks" / "t.parquet").write_bytes(b"y" * 2048)
        report = inspect(root)
        assert report["ticks"]["present"] is True and report["ticks"]["bytes"] == 2048

        # A missing timeframe must be reported, not assumed.
        import shutil
        shutil.rmtree(root / "bars" / "M15")
        assert inspect(root)["bars_complete"] is False

        found = locate(extra=[Path(temporary)])
        assert any(item["path"] == str(root) for item in found), found

    assert inspect(Path(temporary) / "gone")["exists"] is False
    assert human(1536) == "1.5 KB"
    print("inventory demo OK")


if __name__ == "__main__":
    demo()
