"""Local Windows node state, locking, release and dataset checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class NodePaths:
    root: Path
    state_dir: Path
    logs_dir: Path
    state_file: Path
    preflight_file: Path
    lock_file: Path
    stop_file: Path
    node_log: Path
    hunt_log: Path

    @classmethod
    def for_root(cls, root: Path = REPO_ROOT) -> "NodePaths":
        configured = os.environ.get("POPPER_NODE_STATE_DIR")
        state_dir = Path(configured).expanduser() if configured else root / ".popper"
        if not state_dir.is_absolute():
            state_dir = root / state_dir
        logs_dir = state_dir / "logs"
        return cls(
            root=root,
            state_dir=state_dir,
            logs_dir=logs_dir,
            state_file=state_dir / "state.json",
            preflight_file=state_dir / "preflight.json",
            lock_file=state_dir / "worker.lock",
            stop_file=state_dir / "stop.requested",
            node_log=logs_dir / "node.jsonl",
            hunt_log=logs_dir / "hunt.log",
        )

    def ensure(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def append_node_log(paths: NodePaths, event: str, message: str, **fields: Any) -> None:
    paths.ensure()
    row = {"timestamp": utc_now(), "event": event, "message": message, **fields}
    with paths.node_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def process_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        # Access denied still proves that the process exists.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


class NodeAlreadyRunning(RuntimeError):
    pass


class NodeLock:
    """Cross-process lock using atomic file creation and stale-PID recovery."""

    def __init__(self, path: Path, pid: int | None = None):
        self.path = path
        self.pid = pid or os.getpid()
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    existing = int(self.path.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    existing = 0
                if process_exists(existing):
                    raise NodeAlreadyRunning(f"Popper worker already runs as PID {existing}")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(str(self.pid))
            self._owned = True
            return
        raise NodeAlreadyRunning("Popper worker lock could not be acquired")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            owner = int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            owner = None
        if owner == self.pid:
            self.path.unlink(missing_ok=True)
        self._owned = False

    def __enter__(self) -> "NodeLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def parse_retry_after(text: str, default: int = 2700) -> int:
    matches = re.findall(r"PROVIDER_PAUSED\s+retry_after=(\d+)", text)
    value = int(matches[-1]) if matches else default
    return max(60, min(value, 7200))


def current_release(root: Path = REPO_ROOT) -> str | None:
    try:
        tagged = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if tagged.returncode == 0 and tagged.stdout.strip():
            return tagged.stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return commit.stdout.strip() if commit.returncode == 0 and commit.stdout.strip() else None


def inspect_dataset(dataset_dir: Path, expected_id: str) -> dict[str, Any]:
    manifest_path = dataset_dir / "dataset-manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"dataset manifest missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_id = f"{manifest['dataset_version']}@{manifest['dataset_sha256']}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"dataset manifest is invalid: {manifest_path}") from exc
    if actual_id != expected_id:
        raise ValueError("configured dataset identity does not match the received manifest")
    required = (
        dataset_dir / "bars" / "M1",
        dataset_dir / "bars" / "M5",
        dataset_dir / "bars" / "M15",
    )
    missing = [str(path) for path in required if not path.is_dir()]
    if missing:
        raise ValueError("dataset is missing canonical M1/M5/M15 directories")
    return {
        "dataset_id": actual_id,
        "file_count": manifest.get("file_count"),
        "total_size_bytes": manifest.get("total_size_bytes"),
        "manifest": str(manifest_path),
    }


def status_snapshot(paths: NodePaths) -> dict[str, Any]:
    state = read_json(paths.state_file)
    pid = state.get("pid")
    alive = process_exists(pid if isinstance(pid, int) else None)
    status = state.get("status", "not_started")
    if pid and not alive and status not in {"stopped", "not_started"}:
        status = "stale"
    return {**state, "status": status, "alive": alive}
