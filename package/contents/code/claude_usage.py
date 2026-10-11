#!/usr/bin/env python3
"""Read Claude subscription limits from the account signed in to Claude Code.

The access token that Claude Code stores is sent only to Anthropic's usage
endpoint. It is never refreshed or copied: Claude Code renews it while it runs.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

# `cedar_ember` adds the stock of limit resets, the block Claude Code reads.
USAGE_URL = "https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1"
# Anthropic returns the reset stock only to Claude Code, so the request
# presents Claude Code's client headers.
CLIENT_HEADERS = {"User-Agent": "claude-cli/2.1.296 (external, cli)", "x-app": "cli"}
WINDOWS = {"fiveHour": "five_hour", "sevenDay": "seven_day"}
STORED_FIELDS = (*WINDOWS.values(), "cedar_ember")
# The endpoint rate-limits frequent polling, so every widget instance shares
# one record of the last attempt and waits this long before the next one.
MIN_FETCH_INTERVAL_SECONDS = 30
RATE_LIMITED_BACKOFF_SECONDS = 300
MAX_BACKOFF_SECONDS = 3600
LOCK_TIMEOUT_SECONDS = 20

SIGNED_OUT = "Connectez-vous à Claude Code"
EXPIRED = "Session Claude expirée : ouvrez Claude Code"
UNAVAILABLE = "Données Claude indisponibles"
UNREADABLE_CREDENTIALS = "Identifiants Claude Code illisibles"
CACHE_UNAVAILABLE = "Cache du widget inaccessible"


class FetchError(RuntimeError):
    def __init__(self, message: str, retry_after: float = MIN_FETCH_INTERVAL_SECONDS) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Fail on redirects, which would forward the token to another URL."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


OPENER = urllib.request.build_opener(RefuseRedirect)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def credentials_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(Path.home(), ".claude")
    return Path(base) / ".credentials.json"


def cache_path() -> Path:
    override = os.environ.get("CLAUDE_USAGE_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "claude-usage-plasmoid" / "usage.json"


def is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # Integers too large for a float.
        return False


def access_token(path: Path, now: float) -> str:
    try:
        with path.open(encoding="utf-8") as handle:
            credentials = json.load(handle)
    except FileNotFoundError:
        raise RuntimeError(SIGNED_OUT) from None
    except (OSError, ValueError):
        raise RuntimeError(UNREADABLE_CREDENTIALS) from None

    oauth = credentials.get("claudeAiOauth") if isinstance(credentials, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError(SIGNED_OUT)
    # Only visible ASCII can travel in the Authorization header.
    if not all("!" <= character <= "~" for character in token):
        raise RuntimeError(UNREADABLE_CREDENTIALS)

    expires_at = oauth.get("expiresAt")  # Milliseconds.
    if is_number(expires_at) and expires_at / 1000 <= now:
        raise RuntimeError(EXPIRED)
    return token


def retry_after(headers: Any) -> float:
    value = headers.get("Retry-After") if headers is not None else None
    # Rate limiting can persist past a short Retry-After, so it only extends
    # the default backoff.
    if isinstance(value, str) and value.strip().isdigit():
        return min(MAX_BACKOFF_SECONDS, max(RATE_LIMITED_BACKOFF_SECONDS, int(value)))
    return RATE_LIMITED_BACKOFF_SECONDS


def fetch_usage(token: str, timeout: float) -> Any:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
            **CLIENT_HEADERS,
        },
    )
    # Messages never include exception text, which could quote the request.
    try:
        with OPENER.open(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 401:
            raise FetchError(EXPIRED) from None
        if error.code == 403:
            raise FetchError("Accès refusé par Claude") from None
        if error.code == 429:
            raise FetchError("Trop de requêtes à Claude", retry_after(error.headers)) from None
        raise FetchError(f"Claude : erreur HTTP {error.code}") from None
    except ValueError:  # A header value http.client rejects.
        raise FetchError(UNREADABLE_CREDENTIALS) from None
    except (OSError, http.client.HTTPException):  # URLError and timeouts are OSErrors.
        raise FetchError("Claude injoignable") from None
    try:
        return json.loads(body)
    except ValueError:
        raise FetchError("Réponse Claude inattendue") from None


def parse_reset(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        reset = datetime.fromisoformat(value)
    except ValueError:
        return None
    return int(reset.timestamp()) if reset.tzinfo is not None else None


def parse_window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not is_number(raw.get("utilization")):
        return None

    used = min(100.0, max(0.0, float(raw["utilization"])))
    # An unused window has not started yet and has no reset time.
    return {"remainingPercent": 100.0 - used, "resetsAt": parse_reset(raw.get("resets_at"))}


def parse_grants(raw: Any) -> list[dict[str, Any]] | None:
    """Return the resets left in each grant, or None if the stock is unknown."""
    if not isinstance(raw, dict) or raw.get("eligible") is not True:
        return None
    grants = raw.get("grants")
    if not isinstance(grants, list):
        return None
    return [
        {"left": int(grant["resets_left"]), "expiresAt": parse_reset(grant.get("ends_at"))}
        for grant in grants
        if isinstance(grant, dict)
        and is_number(grant.get("resets_left"))
        and grant["resets_left"] >= 1
    ]


def usage_payload(raw: Any, updated_at: float) -> dict[str, Any]:
    windows = {
        key: parse_window(raw.get(name)) if isinstance(raw, dict) else None
        for key, name in WINDOWS.items()
    }
    if all(window is None for window in windows.values()):
        raise RuntimeError("Aucune limite Claude disponible pour ce compte")
    grants = parse_grants(raw.get("cedar_ember"))
    return {"ok": True, "updatedAt": int(updated_at), **windows, "resetGrants": grants}


def as_of(payload: dict[str, Any], now: float) -> dict[str, Any]:
    """Present stored limits at `now`, after resets and grant expiries since the update."""
    current = {key: value for key, value in payload.items() if key != "resetGrants"}
    for key in WINDOWS:
        window = payload[key]
        if window is not None and window["resetsAt"] is not None and window["resetsAt"] <= now:
            current[key] = {"remainingPercent": 100.0, "resetsAt": None}

    grants = payload["resetGrants"]
    if grants is None:
        current["resetCreditsAvailable"] = None
        current["nextResetCreditExpiresAt"] = None
        return current
    usable = [grant for grant in grants if grant["expiresAt"] is None or grant["expiresAt"] > now]
    expiries = [grant["expiresAt"] for grant in usable if grant["expiresAt"] is not None]
    current["resetCreditsAvailable"] = sum(grant["left"] for grant in usable)
    current["nextResetCreditExpiresAt"] = min(expiries) if expiries else None
    return current


def load_state(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, separators=(",", ":"))
    os.replace(temporary, path)


def last_result(state: dict[str, Any]) -> dict[str, Any] | None:
    updated_at = state.get("updatedAt")
    if not is_number(updated_at):
        return None
    try:
        return usage_payload(state.get("limits"), updated_at)
    except RuntimeError:
        return None


def last_warning(state: dict[str, Any]) -> str | None:
    warning = state.get("warning")
    return warning if isinstance(warning, str) and warning else None


def fetch_due(state: dict[str, Any], now: float) -> bool:
    attempted_at, retry_at = state.get("attemptedAt"), state.get("retryAt")
    if not is_number(attempted_at) or not is_number(retry_at):
        return True
    # A clock set back before the last attempt also allows a new one.
    return not attempted_at <= now < retry_at


def outcome(state: dict[str, Any], now: float) -> dict[str, Any]:
    result, warning = last_result(state), last_warning(state)
    if result is None:
        raise RuntimeError(warning or UNAVAILABLE)
    result = as_of(result, now)
    # Keep the last known limits on screen; their age stays visible.
    return {**result, "warning": warning} if warning else result


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Let one helper at a time decide whether to call the endpoint.

    Raises OSError if the lock file cannot be created, and TimeoutError if
    another helper keeps the lock too long.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as handle:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("cache lock busy") from None
                time.sleep(0.1)
        yield


def updated_state(
    state: dict[str, Any], timeout: float, clock: Callable[[], float]
) -> dict[str, Any]:
    """Call the endpoint and return the state to store, timed from its answer."""
    wait, warning = MIN_FETCH_INTERVAL_SECONDS, None
    try:
        raw = fetch_usage(access_token(credentials_path(), clock()), timeout)
        usage_payload(raw, 0)
    except FetchError as error:
        wait, warning = error.retry_after, str(error)
    except RuntimeError as error:  # Missing credentials or a response without limits.
        warning = str(error)

    answered_at = clock()
    if warning is not None:
        return {**state, "attemptedAt": answered_at, "retryAt": answered_at + wait, "warning": warning}
    return {
        "attemptedAt": answered_at,
        "retryAt": answered_at + MIN_FETCH_INTERVAL_SECONDS,
        "updatedAt": int(answered_at),
        "limits": {name: raw.get(name) for name in STORED_FIELDS},
    }


def read_limits(timeout: float, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    path = cache_path()
    try:
        with locked(path):
            # Reading the clock under the lock keeps the attempt recorded by
            # the previous holder in this helper's past.
            now = clock()
            state = load_state(path)
            if fetch_due(state, now):
                # Without a stored attempt the throttle cannot hold, so the
                # endpoint is only called once the attempt is recorded.
                save_state(
                    path, {**state, "attemptedAt": now, "retryAt": now + MIN_FETCH_INTERVAL_SECONDS}
                )
                state = updated_state(state, timeout, clock)
                try:
                    save_state(path, state)
                except OSError:
                    pass  # The recorded attempt still delays the next helper.
    except TimeoutError:  # Another helper is still waiting on the endpoint.
        now, state = clock(), load_state(path)
    except OSError:
        now, state = clock(), {**load_state(path), "warning": CACHE_UNAVAILABLE}
    return outcome(state, now)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--request-id", help="Ignored cache-busting value from Plasma")
    args = parser.parse_args()

    try:
        emit(read_limits(max(2.0, args.timeout)))
        return 0
    except Exception as error:  # Keep the executable data engine contract simple.
        emit({"ok": False, "error": str(error), "updatedAt": int(time.time())})
        return 1


if __name__ == "__main__":
    sys.exit(main())
