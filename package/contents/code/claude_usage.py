#!/usr/bin/env python3
"""Record and read Claude subscription limits from the Claude Code status line.

Claude Code passes `rate_limits` to its status line command on stdin. The
`record` mode stores a sanitized copy of those limits; the default `read` mode
emits them for the Plasma widget. OAuth credentials are never read.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

WINDOWS = ("five_hour", "seven_day")
LOCK_TIMEOUT_SECONDS = 1.0
# Sessions are kept a week after all their windows reset so that elapsed
# windows still display as fully available. The cap bounds the file size and
# evicts sessions whose windows have all reset first.
PRUNE_AFTER_SECONDS = 7 * 86400
MAX_SESSIONS = 512
# Markers usually live until the weekly reset, so in a session with thousands
# of responses a week this cap is the working bound: past it, an idle process
# sharing the session could replay its oldest marker as new.
MAX_MARKERS = 4096


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def cache_path() -> Path:
    override = os.environ.get("CLAUDE_USAGE_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "claude-usage-plasmoid" / "rate_limits.json"


def is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # Integers too large for a float.
        return False


def positive_number(value: Any) -> float | None:
    return float(value) if is_number(value) and value > 0 else None


def rounded_percent(value: float) -> int:
    # Match QML's Math.round so the status line and the widget agree.
    return math.floor(value + 0.5)


def parse_window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not is_number(raw.get("used_percentage")):
        return None

    resets_at = positive_number(raw.get("resets_at"))
    if resets_at is None:
        return None

    return {
        "used_percentage": min(100.0, max(0.0, float(raw["used_percentage"]))),
        "resets_at": int(resets_at),
    }


def parse_windows(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    return {
        name: window
        for name in WINDOWS
        if (window := parse_window(raw.get(name))) is not None
    }


def parse_marker(raw: Any) -> list[float] | None:
    """Validate a `[cost, expires_at]` pair recorded for a past response."""
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    cost, expires_at = positive_number(raw[0]), positive_number(raw[1])
    return None if cost is None or expires_at is None else [cost, int(expires_at)]


def parse_session(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    observed_at = positive_number(raw.get("observed_at"))
    cost = positive_number(raw.get("cost"))
    markers = raw.get("response_markers")
    markers = [parse_marker(marker) for marker in markers] if isinstance(markers, list) else []
    windows = parse_windows(raw.get("windows"))
    if observed_at is None or cost is None or not markers or None in markers or not windows:
        return None
    return {"observed_at": observed_at, "cost": cost, "response_markers": markers, "windows": windows}


def response_marker(status: dict[str, Any]) -> float | None:
    """Return the session's cumulative API cost, which grows with each response.

    It is 0 before the session's first response, for example right after
    /clear, when the process can only show limits left from another session.
    """
    cost = status.get("cost")
    return positive_number(cost.get("total_cost_usd") if isinstance(cost, dict) else None)


def observation_time(status: dict[str, Any], now: float) -> float | None:
    """Date the session's latest exchange with its transcript's modification time.

    The status line JSON has no timestamp. Only the transcript metadata is read.
    """
    transcript = status.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return None
    try:
        return min(now, os.stat(transcript).st_mtime)
    except OSError:
        return None


def merged_sessions(
    sessions: dict[str, dict[str, Any]],
    session_id: str,
    rate_limits: Any,
    marker: float,
    observed_at: float,
    now: float,
) -> dict[str, dict[str, Any]] | None:
    """Record a session's limits, or return None if nothing changed.

    Every Claude Code session re-runs its status line with the limits from its
    own last API response, even when idle, and its transcript also changes
    without API calls (local commands, failed requests, a window dropped at
    its reset). Only a new API response, shown by a response marker never seen
    for this session, takes a new observation time. Keeping every marker
    covers one session open in several processes, where each process replays
    its own last marker.
    """
    # A response replaces the session's windows. Claude Code omits a window
    # only once it resets, so a missing window has nothing left to keep.
    windows = {
        name: window
        for name, window in parse_windows(rate_limits).items()
        if window["resets_at"] > now
    }
    previous = sessions.get(session_id)
    # A replayed response can only matter while its windows are live, so its
    # marker is kept until then.
    seen = [
        seen_marker
        for seen_marker in (previous["response_markers"] if previous is not None else [])
        if seen_marker[1] > now
    ]
    if not windows or marker in (cost for cost, _ in seen):
        return None

    markers = [*seen, [marker, max(window["resets_at"] for window in windows.values())]]
    record = {
        "observed_at": observed_at,
        "cost": marker,
        "response_markers": markers[-MAX_MARKERS:],
        "windows": windows,
    }
    # A recorder that waited on the lock can hold an older response than the
    # one already stored: remember it as seen, but keep the newer limits. The
    # cost, which only grows within a process, breaks transcript time ties.
    if previous is not None and (observed_at, marker) < (previous["observed_at"], previous["cost"]):
        record = {**previous, "response_markers": record["response_markers"]}
    merged = {**sessions, session_id: record}

    def last_reset(item: tuple[str, dict[str, Any]]) -> int:
        return max(window["resets_at"] for window in item[1]["windows"].values())

    remembered = [item for item in merged.items() if last_reset(item) > now - PRUNE_AFTER_SECONDS]
    recent = sorted(
        remembered,
        key=lambda item: (last_reset(item) > now, item[1]["observed_at"]),
        reverse=True,
    )
    kept = dict(recent[:MAX_SESSIONS])
    return kept if session_id in kept else None


def current_windows(
    sessions: dict[str, dict[str, Any]], now: float
) -> dict[str, dict[str, Any]]:
    """Pick, for each window, the most recently observed value across sessions.

    Windows that have not reset yet take precedence over elapsed ones, and ties
    resolve to the higher usage.
    """
    current = {}
    for name in WINDOWS:
        candidates = [
            (window["resets_at"] > now, session["observed_at"], window["used_percentage"], window)
            for session in sessions.values()
            if (window := session["windows"].get(name)) is not None
        ]
        if candidates:
            _, observed_at, _, window = max(candidates, key=lambda candidate: candidate[:3])
            current[name] = {**window, "observed_at": observed_at}
    return current


def widget_window(window: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    if window is None:
        return None

    used, resets_at = window["used_percentage"], window["resets_at"]
    observed_at = window["observed_at"]
    if resets_at <= now:
        # The window has rolled over since Claude Code last reported it, so
        # its fresh allowance is known as of the reset time.
        used, observed_at, resets_at = 0.0, resets_at, None

    return {
        "remainingPercent": 100.0 - used,
        "resetsAt": resets_at,
        "observedAt": observed_at,
    }


def load_sessions(path: Path) -> dict[str, dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        return {}
    raw = snapshot.get("sessions") if isinstance(snapshot, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {
        str(session_id): session
        for session_id, value in raw.items()
        if (session := parse_session(value)) is not None
    }


def save_sessions(path: Path, sessions: dict[str, dict[str, Any]]) -> None:
    # Called under `locked`; a fixed name means an interrupted run leaves at
    # most one stale file, which the next save overwrites.
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump({"sessions": sessions}, handle, separators=(",", ":"))
    os.replace(temporary, path)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Serialize snapshot updates across concurrent Claude Code sessions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as handle:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("snapshot lock busy") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def record_status(path: Path, raw_input: bytes, now: float) -> dict[str, dict[str, Any]]:
    status = json.loads(raw_input)
    session_id = status.get("session_id") if isinstance(status, dict) else None
    observed_at = observation_time(status, now) if session_id else None
    marker = response_marker(status) if session_id else None
    if not isinstance(session_id, str) or observed_at is None or marker is None:
        return load_sessions(path)

    with locked(path):
        sessions = load_sessions(path)
        merged = merged_sessions(
            sessions, session_id, status.get("rate_limits"), marker, observed_at, now
        )
        if merged is not None:
            save_sessions(path, merged)
            sessions = merged
    return sessions


def read_limits(path: Path, now: float) -> dict[str, Any]:
    windows = current_windows(load_sessions(path), now)
    if not windows:
        raise RuntimeError(
            "Aucune donnée : configurez la status line Claude Code (voir README)"
        )

    five_hour = widget_window(windows.get("five_hour"), now)
    seven_day = widget_window(windows.get("seven_day"), now)
    return {
        "ok": True,
        # The least recently observed value bounds how stale the widget can be.
        "updatedAt": min(
            window["observedAt"] for window in (five_hour, seven_day) if window is not None
        ),
        "fiveHour": five_hour,
        "sevenDay": seven_day,
    }


def default_status_text(sessions: dict[str, dict[str, Any]], now: float) -> str:
    windows = current_windows(sessions, now)
    parts = []
    for name, label in (("five_hour", "5h"), ("seven_day", "7j")):
        window = widget_window(windows.get(name), now)
        if window is not None:
            parts.append(f"{label} {rounded_percent(window['remainingPercent'])}%")
    return "Claude " + " · ".join(parts) + " restant" if parts else "Claude"


def run_wrapped(command: list[str], raw_input: bytes) -> bytes | None:
    """Run the user's status line command, or return None if it fails."""
    process: subprocess.Popen[bytes] | None = None
    cancelled: list[int] = []

    def stop(signum: int) -> None:
        if process is not None:
            process.terminate()
        os._exit(128 + signum)

    # Claude Code cancels an in-flight status line when a new update arrives;
    # pass that on to the wrapped command, as Claude Code would signal it
    # unwrapped. A signal that arrives while it launches waits for its pid.
    def cancel(signum: int, _frame: Any) -> None:
        if process is None:
            cancelled.append(signum)
        else:
            stop(signum)

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, cancel)

    try:
        # It stays in this process group, the terminal's foreground group, so
        # it can use /dev/tty as it would unwrapped.
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    except OSError:
        process = None
    if cancelled:
        stop(cancelled[0])
    if process is None:
        return None

    output, _ = process.communicate(raw_input)
    return output if process.returncode == 0 else None


def record(command: list[str]) -> int:
    raw_input = sys.stdin.buffer.read()
    now = time.time()
    path = cache_path()

    # A failure here must never break the user's status line.
    try:
        sessions = record_status(path, raw_input, now)
    except Exception:
        try:
            sessions = load_sessions(path)
        except Exception:
            sessions = {}

    if command:
        output = run_wrapped(command, raw_input)
        if output is not None:
            sys.stdout.buffer.write(output)
            return 0

    print(default_status_text(sessions, now))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="mode")
    recorder = subcommands.add_parser(
        "record", help="Status line mode: store rate_limits from stdin"
    )
    recorder.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Optional status line command to run after '--' with the same stdin",
    )
    parser.add_argument("--request-id", help="Ignored cache-busting value from Plasma")
    args = parser.parse_args()

    if args.mode == "record":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        return record(command)

    try:
        emit(read_limits(cache_path(), time.time()))
        return 0
    except Exception as error:  # Keep the executable data engine contract simple.
        emit({"ok": False, "error": str(error), "updatedAt": int(time.time())})
        return 1


if __name__ == "__main__":
    sys.exit(main())
