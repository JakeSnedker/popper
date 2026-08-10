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
    read_exploit,
    read_json,
    read_switch,
    status_snapshot,
    utc_now,
    write_exploit,
    write_json_atomic,
    write_switch,
)


CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0)
# One dashboard requeue request re-tests at most this many stranded
# hypotheses, so a sweep cannot monopolise a node for hours.
REQUEUE_LIMIT = 25


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
    if settings.research_mode not in ("explore", "balanced", "exploit", "auto"):
        raise RuntimeError(f"invalid POPPER_RESEARCH_MODE={settings.research_mode}")
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
        # Three CLI probes at up to 30s each, plus interpreter start. 90s made a
        # cold first run on a new operator machine fail as "LLM providers" when
        # the CLIs were merely slow to answer.
        timeout=180,
    )
    safe = " | ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
    if result.returncode != 0:
        raise RuntimeError(safe or "no zero-marginal-cost LLM route is available")
    return safe or "LLM routing is available"


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
        _check("LLM providers", _check_subscriptions),
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


def _switch_on(paths: NodePaths) -> bool:
    return read_switch(paths) == "on"


def _exploit_on(paths: NodePaths) -> bool:
    return read_exploit(paths) == "on"


def _sync_remote_switch(paths: NodePaths) -> None:
    """Mirror the shared fleet flags for THIS machine into local files, so a GUI
    toggle on another computer takes effect here.

    Local CLI toggles write the file first, then publish to the fleet, so the
    remote value is the shared source of truth. Only values the query actually
    returned are adopted (a missing remote row means this node has not reported
    yet; leave the local files alone).
    """
    flags = node_presence.read_node_flags(settings.machine_id) if node_presence.enabled() else {}
    if not flags:
        return
    value = flags.get("switch")
    if value in {"on", "off"} and value != read_switch(paths):
        write_switch(paths, value)
        append_node_log(paths, "switch_sync", f"Adopted remote switch {value}")
    exploit = flags.get("exploit")
    if exploit in {"on", "off"} and exploit != read_exploit(paths):
        write_exploit(paths, exploit)
        append_node_log(paths, "exploit_sync", f"Adopted remote exploit {exploit}")
    if flags.get("requeue_requested_at") and not flags.get("requeue_finished_at"):
        _run_requeue(paths)


def _run_requeue(paths: NodePaths) -> None:
    """Sweep the shared stranded-hypothesis backlog because the dashboard asked.

    The backlog is fleet-wide: nothing records which machine stranded a
    hypothesis, so picking a node picks who does the work, not whose work it is.
    Bounded per sweep so one request cannot monopolise a node for hours.
    """
    append_node_log(paths, "requeue_start", "Requeue requested from the dashboard")
    _update_state(paths, publish=True, status="requeue", heartbeat_at=utc_now())
    try:
        from popper.research.requeue import requeue

        summary = requeue(limit=REQUEUE_LIMIT)
        verdicts = summary.get("verdicts") or {}
        result = (f"re-tested {sum(verdicts.values())} "
                  f"({', '.join(f'{k}={v}' for k, v in sorted(verdicts.items())) or 'none'}); "
                  f"{summary.get('orphaned', 0)} need re-synthesis; "
                  f"{len(summary.get('failed') or [])} failed")
    except Exception as exc:  # noqa: BLE001 - a bad sweep must not kill the node
        result = f"requeue failed: {type(exc).__name__}: {exc}"
    append_node_log(paths, "requeue_done", result)
    node_presence.finish_requeue(settings.machine_id, result)


def _wait_until_allowed(paths: NodePaths, seconds: int) -> bool:
    """Sleep up to `seconds`, exiting early if the node is told to stop.

    While the switch is off we still publish heartbeats so the fleet sees the
    node is alive but parked. Returns False only when a hard stop is requested.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _stop_requested(paths):
            return False
        _update_state(paths, heartbeat_at=utc_now(), parked=not _switch_on(paths))
        time.sleep(min(5, max(0.1, deadline - time.monotonic())))
    return True


def _wait_for_backoff(paths: NodePaths, seconds: int) -> bool:
    return _wait_until_allowed(paths, seconds)


def _wait_for_resume(paths: NodePaths) -> None:
    """Block the worker while parked, publishing heartbeats, until the switch
    is back on. A hard stop breaks the outer loop independently of this call.
    """
    while _stop_requested(paths) is False:
        _sync_remote_switch(paths)
        _update_state(paths, heartbeat_at=utc_now(), parked=True)
        if _switch_on(paths):
            append_node_log(paths, "switch_on", "Node resumed by user switch")
            return
        time.sleep(5)


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


def run_worker(minutes: float, iterations: int, mode: str,
               smoke_seconds: int = 0) -> int:
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
        research_mode=mode,
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
            # Adopt any remote (GUI) switch change for this machine.
            _sync_remote_switch(paths)
            if not _switch_on(paths):
                # Node is parked: keep the process alive and heartbeating but
                # do not launch any hunt (no new LLM calls) until switch is on.
                parked_since = read_json(paths.state_file).get("parked_at")
                _update_state(
                    paths,
                    publish=True,
                    status="parked",
                    parked=True,
                    child_pid=None,
                    parked_at=parked_since or utc_now(),
                    heartbeat_at=utc_now(),
                )
                # Wait for the switch to turn back on (unbounded), or a hard stop.
                _wait_for_resume(paths)
                continue

            # Exploit refinement is the CPU-heavy half and makes no LLM calls, so
            # switching it off means "keep proposing, give me my cores back" —
            # explore/wildcard only, rather than stopping the node.
            hunt_mode = mode if _exploit_on(paths) else "explore"
            command = [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_hunt.py"),
                "--minutes",
                str(minutes),
                "--iterations",
                str(iterations),
                "--mode",
                hunt_mode,
            ]
            with paths.hunt_log.open("a", encoding="utf-8", buffering=1) as log:
                log.write(f"\n[{utc_now()}] starting hunt (mode={hunt_mode})\n")
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
                    parked=False,
                    parked_at=None,
                    heartbeat_at=utc_now(),
                )
                while child.poll() is None:
                    _sync_remote_switch(paths)
                    if _stop_requested(paths):
                        _update_state(paths, publish=True, status="stopping", heartbeat_at=utc_now())
                        _terminate_tree(child.pid)
                        child.wait(timeout=30)
                        break
                    if not _switch_on(paths):
                        # Park after this hunt finishes rather than terminating
                        # it mid-run, so no prompt work is lost. The outer loop
                        # gate then holds the node parked until switched back on.
                        _update_state(paths, publish=True, status="parked", parked=True, heartbeat_at=utc_now())
                        child.wait()
                        append_node_log(paths, "switch_off", "Hunt finished; node parked by user switch")
                        child = None
                        break
                    _update_state(paths, heartbeat_at=utc_now())
                    time.sleep(5)
                return_code = child.poll() if child is not None else 0
                child = None

            if _stop_requested(paths):
                break
            if not _switch_on(paths):
                continue
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


def start_node(minutes: float, iterations: int, mode: str,
               smoke_seconds: int = 0) -> int:
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
                "--mode",
                mode,
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
        _update_state(
            paths, publish=True, status="stopped", pid=None, child_pid=None,
            last_error=None,
        )
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
    _update_state(
        paths, publish=True, status="stopped", pid=None, child_pid=None,
        stopped_at=utc_now(), last_error=None,
    )
    paths.stop_file.unlink(missing_ok=True)
    print("Popper node stopped.")
    return 0


def set_switch(value: str) -> int:
    """Persist the operator on/off switch. OFF lets the current hunt finish,
    then parks the node so it stops making LLM calls until switched back on.
    """
    paths = NodePaths.for_root(REPO_ROOT)
    paths.ensure()
    write_switch(paths, value)
    append_node_log(paths, "switch_set", f"Operator switch set to {value}")
    # Publish to the shared fleet so the dashboard (and other machines) see it,
    # and so a remote worker that re-syncs stays in lockstep.
    error = node_presence.set_switch(settings.machine_id, value)
    _update_state(paths, publish=True, switch=value, switch_requested_at=utc_now())
    print(f"Popper switch is now {value.upper()}.")
    if value == "off":
        print("Current hunt will finish; no new hunts will start until it is turned back on.")
    if error:
        print(f"Note: fleet sync reported: {error}")
    return 0


def set_exploit(value: str) -> int:
    """Persist the CPU-only exploit engine toggle. OFF keeps the node proposing
    new hypotheses but stops it refining survivors, freeing the cores."""
    paths = NodePaths.for_root(REPO_ROOT)
    paths.ensure()
    write_exploit(paths, value)
    append_node_log(paths, "exploit_set", f"Operator exploit set to {value}")
    error = node_presence.set_exploit(settings.machine_id, value)
    _update_state(paths, publish=True, exploit=value)
    print(f"Popper exploit engine is now {value.upper()}.")
    if value == "off":
        print("The next hunt will explore only; survivor refinement is paused.")
    if error:
        print(f"Note: fleet sync reported: {error}")
    return 0


def print_status(as_json: bool) -> int:
    paths = NodePaths.for_root(REPO_ROOT)
    state = status_snapshot(paths)
    preflight = read_json(paths.preflight_file)
    value = {**state, "switch": read_switch(paths), "exploit": read_exploit(paths),
             "preflight_ok": preflight.get("ok"), "paths": {
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
    print(f"Mode:        {value.get('research_mode') or settings.research_mode}")
    print(f"Switch:      {'ON' if value.get('switch') == 'on' else 'OFF (parked)'}")
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
    start.add_argument("--mode", choices=("explore", "balanced", "exploit", "auto"),
                       default=settings.research_mode)
    start.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)

    worker = commands.add_parser("worker", help="internal foreground worker controller")
    worker.add_argument("--minutes", type=float, default=25)
    worker.add_argument("--iterations", type=int, default=20)
    worker.add_argument("--mode", choices=("explore", "balanced", "exploit", "auto"),
                        default=settings.research_mode)
    worker.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)

    stop = commands.add_parser("stop", help="stop the background worker")
    stop.add_argument("--timeout", type=int, default=30)
    stop.add_argument("--force", action="store_true")

    status = commands.add_parser("status", help="show heartbeat and last error")
    status.add_argument("--json", action="store_true")

    switch = commands.add_parser("switch", help="turn the node on or off (off parks after the current hunt)")
    switch.add_argument("value", choices=("on", "off"))

    exploit = commands.add_parser(
        "exploit",
        help="turn the CPU-only survivor-refinement engine on or off (node keeps exploring)",
    )
    exploit.add_argument("value", choices=("on", "off"))

    commands.add_parser(
        "requeue",
        help="sweep the shared backlog of hypotheses stranded between synthesis and verdict",
    )

    args = parser.parse_args()
    if args.command == "preflight":
        result = run_preflight(full_dataset=args.full_dataset)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print_preflight(result)
        return 0 if result["ok"] else 1
    if args.command == "start":
        return start_node(args.minutes, args.iterations, args.mode, args.smoke_seconds)
    if args.command == "worker":
        return run_worker(args.minutes, args.iterations, args.mode, args.smoke_seconds)
    if args.command == "stop":
        return stop_node(args.timeout, args.force)
    if args.command == "status":
        return print_status(args.json)
    if args.command == "switch":
        return set_switch(args.value)
    if args.command == "exploit":
        return set_exploit(args.value)
    if args.command == "requeue":
        _run_requeue(NodePaths.for_root(REPO_ROOT))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
