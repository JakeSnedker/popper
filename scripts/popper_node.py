"""Windows operator control for Popper setup, preflight and worker lifecycle."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from popper import node_presence
from popper.config import settings
from popper.node_operator import (
    NodeAlreadyRunning,
    NodeLock,
    NodePaths,
    REPO_ROOT,
    append_node_log,
    current_release,
    inspect_dataset,
    parse_retry_after,
    process_exists,
    read_json,
    status_snapshot,
    utc_now,
    write_json_atomic,
)


CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)


def _dataset_dir() -> Path:
    configured = settings.dataset_dir.strip()
    path = Path(configured).expanduser() if configured else REPO_ROOT / "data" / "ftmo-us30-v1"
    return path if path.is_absolute() else REPO_ROOT / path


def _check(name: str, action: Callable[[], str]) -> dict[str, Any]:
    try:
        detail = action()
    except Exception as exc:  # noqa: BLE001 - preflight must report every failed boundary.
        return {"name": name, "ok": False, "detail": str(exc)}
    return {"name": name, "ok": True, "detail": detail}


def _check_python() -> str:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    return f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _check_release() -> str:
    actual = current_release(REPO_ROOT)
    if not actual:
        raise RuntimeError("Git release identity is unavailable")
    if not settings.release_id:
        raise RuntimeError("POPPER_RELEASE_ID is not configured")
    if settings.release_id != actual:
        raise RuntimeError(
            f"configured release does not match this checkout ({settings.release_id} != {actual})"
        )
    return actual


def _check_config() -> str:
    missing = []
    for name, value in (
        ("POPPER_SUPABASE_URL", settings.supabase_url),
        ("POPPER_SUPABASE_KEY", settings.supabase_key),
        ("POPPER_SUPABASE_EMAIL", settings.supabase_email),
        ("POPPER_SUPABASE_PASSWORD", settings.supabase_password),
        ("POPPER_MACHINE_ID", settings.machine_id),
        ("POPPER_RELEASE_ID", settings.release_id),
        ("POPPER_DATASET_ID", settings.dataset_id),
    ):
        if not str(value).strip():
            missing.append(name)
    if missing:
        raise RuntimeError("missing settings: " + ", ".join(missing))
    if settings.store != "supabase" or not settings.auth_required:
        raise RuntimeError("POPPER_STORE=supabase and POPPER_AUTH_REQUIRED=true are required")
    return "private operator configuration is complete"


def _check_dataset(full: bool) -> str:
    result = inspect_dataset(_dataset_dir(), settings.dataset_id)
    if full:
        from popper.data.canonical_dataset import verify_dataset

        verified = verify_dataset(_dataset_dir())
        if not verified.get("valid"):
            raise RuntimeError("full dataset verification failed")
        return (
            f"full hash verified: {verified['dataset_version']} "
            f"({verified['file_count']} files)"
        )
    return f"manifest identity verified ({result.get('file_count') or '?'} files)"


def _check_subscriptions() -> str:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_subscription_auth.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    safe = " | ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
    if result.returncode != 0:
        raise RuntimeError(safe or "no subscription CLI is authenticated on this machine")
    return safe or "subscription routing is available"


def _check_supabase() -> str:
    from popper.memory.supabase_client import create_configured_client

    client = create_configured_client()
    rows = client.rpc("popper_access_probe").execute().data
    if len(rows) != 1:
        raise RuntimeError("operator is not an active Popper member")
    role = rows[0]["role"]
    if not rows[0]["can_write"]:
        raise RuntimeError(f"role={role} is read-only; a worker requires admin or researcher")
    return f"authenticated role={role} with attributed write permission"


def _check_frontend() -> str:
    index = REPO_ROOT / "gui" / "dist" / "index.html"
    if not index.is_file():
        raise RuntimeError("dashboard build is missing; run npm --prefix gui run build")
    return "dashboard production build present"


def run_preflight(full_dataset: bool = False) -> dict[str, Any]:
    paths = NodePaths.for_root(REPO_ROOT)
    paths.ensure()
    checks = [
        _check("Python", _check_python),
        _check("Configuration", _check_config),
        _check("Release", _check_release),
        _check("Dataset", lambda: _check_dataset(full_dataset)),
        _check("Subscriptions", _check_subscriptions),
        _check("Supabase", _check_supabase),
        _check("Dashboard", _check_frontend),
    ]
    result = {
        "checked_at": utc_now(),
        "ok": all(item["ok"] for item in checks),
        "full_dataset": full_dataset,
        "checks": checks,
    }
    write_json_atomic(paths.preflight_file, result)
    return result


def print_preflight(result: dict[str, Any]) -> None:
    print("\nPopper node preflight")
    print("=" * 72)
    for item in result["checks"]:
        marker = "PASS" if item["ok"] else "FAIL"
        print(f"[{marker}] {item['name']}: {item['detail']}")
    print("=" * 72)
    print("READY" if result["ok"] else "NOT READY")


def _update_state(paths: NodePaths, publish: bool = False, **updates: Any) -> dict[str, Any]:
    """Write local node state, and mirror it to the shared fleet view.

    `publish=True` forces an immediate push for lifecycle transitions the team
    should see at once (start, backoff, crash, stop). Routine 5-second
    heartbeats are throttled inside node_presence.
    """
    state = read_json(paths.state_file)
    state.update(updates)
    state["updated_at"] = utc_now()
    write_json_atomic(paths.state_file, state)
    error = node_presence.publish(state, force=publish)
    if error:
        append_node_log(paths, "presence_error", "Fleet heartbeat failed", detail=error)
    return state


def _tail(path: Path, size: int = 100_000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            length = handle.tell()
            handle.seek(max(0, length - size))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _stop_requested(paths: NodePaths) -> bool:
    return paths.stop_file.exists()


def _wait_for_backoff(paths: NodePaths, seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _stop_requested(paths):
            return False
        _update_state(paths, heartbeat_at=utc_now())
        time.sleep(min(5, max(0.1, deadline - time.monotonic())))
    return True


def _terminate_tree(pid: int, force: bool = True) -> None:
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, capture_output=True, timeout=20)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def run_worker(minutes: float, iterations: int, smoke_seconds: int = 0) -> int:
    paths = NodePaths.for_root(REPO_ROOT)
    paths.ensure()
    lock = NodeLock(paths.lock_file)
    try:
        lock.acquire()
    except NodeAlreadyRunning as exc:
        print(str(exc))
        return 2

    paths.stop_file.unlink(missing_ok=True)
    pid = os.getpid()
    append_node_log(paths, "worker_start", "Popper worker started", pid=pid)
    _update_state(
        paths,
        publish=True,
        status="running",
        pid=pid,
        started_at=utc_now(),
        heartbeat_at=utc_now(),
        release_id=settings.release_id,
        machine_id=settings.machine_id,
        last_error=None,
    )

    child: subprocess.Popen[Any] | None = None
    try:
        if smoke_seconds > 0:
            append_node_log(paths, "smoke_start", "Bounded worker smoke test started")
            _update_state(paths, publish=True, status="smoke_test", heartbeat_at=utc_now())
            deadline = time.monotonic() + smoke_seconds
            while time.monotonic() < deadline and not _stop_requested(paths):
                _update_state(paths, heartbeat_at=utc_now())
                time.sleep(1)
            append_node_log(paths, "smoke_complete", "Bounded worker smoke test completed")
            return 0

        while not _stop_requested(paths):
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_hunt.py"),
                "--minutes",
                str(minutes),
                "--iterations",
                str(iterations),
                "--explore-heavy",
            ]
            with paths.hunt_log.open("a", encoding="utf-8", buffering=1) as log:
                log.write(f"\n[{utc_now()}] starting hunt\n")
                child = subprocess.Popen(
                    command,
                    cwd=REPO_ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NEW_PROCESS_GROUP,
                )
                _update_state(
                    paths,
                    publish=True,
                    status="researching",
                    child_pid=child.pid,
                    heartbeat_at=utc_now(),
                )
                while child.poll() is None:
                    if _stop_requested(paths):
                        _update_state(paths, publish=True, status="stopping", heartbeat_at=utc_now())
                        _terminate_tree(child.pid)
                        child.wait(timeout=30)
                        break
                    _update_state(paths, heartbeat_at=utc_now())
                    time.sleep(5)
                return_code = child.poll()
                child = None

            if _stop_requested(paths):
                break
            if return_code == 0:
                append_node_log(paths, "hunt_complete", "Research window completed")
                _update_state(
                    paths,
                    publish=True,
                    status="idle",
                    child_pid=None,
                    heartbeat_at=utc_now(),
                    last_exit_code=0,
                    last_error=None,
                )
                if not _wait_for_backoff(paths, 10):
                    break
                continue

            if return_code == 75:
                delay = parse_retry_after(_tail(paths.hunt_log))
                append_node_log(
                    paths,
                    "provider_backoff",
                    "Provider circuit requested backoff",
                    seconds=delay,
                )
                _update_state(
                    paths,
                    publish=True,
                    status="provider_backoff",
                    child_pid=None,
                    heartbeat_at=utc_now(),
                    retry_after_seconds=delay,
                    last_exit_code=75,
                    last_error=None,
                )
                if not _wait_for_backoff(paths, delay):
                    break
                continue

            delay = 300
            append_node_log(
                paths,
                "hunt_error",
                "Research process failed; retry scheduled",
                exit_code=return_code,
                seconds=delay,
            )
            _update_state(
                paths,
                publish=True,
                status="error_backoff",
                child_pid=None,
                heartbeat_at=utc_now(),
                retry_after_seconds=delay,
                last_exit_code=return_code,
                last_error=f"research process exited with code {return_code}",
            )
            if not _wait_for_backoff(paths, delay):
                break
    except Exception as exc:  # noqa: BLE001 - preserve operational failure state.
        append_node_log(paths, "worker_crash", "Worker controller failed", error=type(exc).__name__)
        _update_state(paths, publish=True, status="crashed", last_error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        if child is not None and child.poll() is None:
            _terminate_tree(child.pid)
        paths.stop_file.unlink(missing_ok=True)
        _update_state(
            paths,
            publish=True,
            status="stopped",
            pid=None,
            child_pid=None,
            stopped_at=utc_now(),
            heartbeat_at=utc_now(),
        )
        append_node_log(paths, "worker_stop", "Popper worker stopped")
        lock.release()
    return 0


def start_node(minutes: float, iterations: int, smoke_seconds: int = 0) -> int:
    paths = NodePaths.for_root(REPO_ROOT)
    paths.ensure()
    state = status_snapshot(paths)
    if state["alive"]:
        print(f"Popper node is already running (PID {state.get('pid')}).")
        return 0

    result = run_preflight(full_dataset=False)
    print_preflight(result)
    if not result["ok"]:
        return 1

    paths.stop_file.unlink(missing_ok=True)
    boot_log = paths.logs_dir / "boot.log"
    with boot_log.open("a", encoding="utf-8") as handle:
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker",
                "--minutes",
                str(minutes),
                "--iterations",
                str(iterations),
                *(["--smoke-seconds", str(smoke_seconds)] if smoke_seconds > 0 else []),
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            close_fds=True,
        )
    _update_state(paths, publish=True, status="starting", pid=child.pid, heartbeat_at=utc_now())
    time.sleep(2)
    if not process_exists(child.pid):
        print(f"Popper node failed to start. See {boot_log}")
        return 1
    label = "smoke test" if smoke_seconds > 0 else "node"
    print(f"Popper {label} started (PID {child.pid}).")
    print(f"Status: {REPO_ROOT / 'operator' / 'status.ps1'}")
    return 0


def stop_node(timeout: int, force: bool) -> int:
    paths = NodePaths.for_root(REPO_ROOT)
    paths.ensure()
    state = status_snapshot(paths)
    pid = state.get("pid") if isinstance(state.get("pid"), int) else None
    if not pid or not state["alive"]:
        paths.stop_file.unlink(missing_ok=True)
        _update_state(paths, publish=True, status="stopped", pid=None, child_pid=None)
        print("Popper node is already stopped.")
        return 0

    paths.stop_file.write_text(utc_now(), encoding="ascii")
    print(f"Stop requested for PID {pid}.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process_exists(pid):
        time.sleep(1)
    if process_exists(pid):
        if not force:
            print("Worker did not stop in time. Re-run with --force if necessary.")
            return 1
        _terminate_tree(pid)
        time.sleep(1)
    _update_state(paths, publish=True, status="stopped", pid=None, child_pid=None, stopped_at=utc_now())
    paths.stop_file.unlink(missing_ok=True)
    print("Popper node stopped.")
    return 0


def print_status(as_json: bool) -> int:
    paths = NodePaths.for_root(REPO_ROOT)
    state = status_snapshot(paths)
    preflight = read_json(paths.preflight_file)
    value = {**state, "preflight_ok": preflight.get("ok"), "paths": {
        "state": str(paths.state_file),
        "node_log": str(paths.node_log),
        "hunt_log": str(paths.hunt_log),
    }}
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    print("\nPopper node status")
    print("=" * 72)
    print(f"State:       {value.get('status', 'not_started').upper()}")
    print(f"Process:     {'alive' if value.get('alive') else 'not running'}")
    print(f"PID:         {value.get('pid') or '-'}")
    print(f"Machine:     {value.get('machine_id') or settings.machine_id or '-'}")
    print(f"Release:     {value.get('release_id') or settings.release_id or '-'}")
    print(f"Heartbeat:   {value.get('heartbeat_at') or '-'}")
    print(f"Preflight:   {'PASS' if value.get('preflight_ok') else 'not passed'}")
    print(f"Last error:  {value.get('last_error') or '-'}")
    print(f"Node log:    {paths.node_log}")
    print(f"Hunt log:    {paths.hunt_log}")
    print("=" * 72)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser("preflight", help="verify this machine without researching")
    preflight.add_argument("--full-dataset", action="store_true")
    preflight.add_argument("--json", action="store_true")

    start = commands.add_parser("start", help="preflight and start one background worker")
    start.add_argument("--minutes", type=float, default=25)
    start.add_argument("--iterations", type=int, default=20)
    start.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)

    worker = commands.add_parser("worker", help="internal foreground worker controller")
    worker.add_argument("--minutes", type=float, default=25)
    worker.add_argument("--iterations", type=int, default=20)
    worker.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)

    stop = commands.add_parser("stop", help="stop the background worker")
    stop.add_argument("--timeout", type=int, default=30)
    stop.add_argument("--force", action="store_true")

    status = commands.add_parser("status", help="show heartbeat and last error")
    status.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.command == "preflight":
        result = run_preflight(full_dataset=args.full_dataset)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_preflight(result)
        return 0 if result["ok"] else 1
    if args.command == "start":
        return start_node(args.minutes, args.iterations, args.smoke_seconds)
    if args.command == "worker":
        return run_worker(args.minutes, args.iterations, args.smoke_seconds)
    if args.command == "stop":
        return stop_node(args.timeout, args.force)
    if args.command == "status":
        return print_status(args.json)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
