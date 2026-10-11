#!/usr/bin/env python3

import contextlib
import email.utils
import fcntl
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request


HELPER_PATH = (
    Path(__file__).parent.parent / "package" / "contents" / "code" / "claude_usage.py"
)
SPEC = importlib.util.spec_from_file_location("claude_usage", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
CLAUDE_USAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLAUDE_USAGE)

NOW = 1_800_000_000.0
FIVE_HOUR_RESET = "2027-01-15T13:00:00.315319+00:00"
FIVE_HOUR_RESET_AT = 1_800_018_000
SEVEN_DAY_RESET = "2027-01-20T08:00:00+00:00"
SEVEN_DAY_RESET_AT = 1_800_432_000
GRANT_END = "2027-01-22T08:00:00+00:00"
GRANT_END_AT = 1_800_604_800
HUGE = 10**400
TOKEN = "secret-token"


def grant(left, ends_at=GRANT_END):
    return {"id": "launch", "resets_total": left, "resets_left": left, "ends_at": ends_at}


def stock(*grants, eligible=True):
    return {"eligible": eligible, "grants": list(grants)}


# The shape returned by the usage endpoint, trimmed to the fields read here.
USAGE = {
    "five_hour": {"utilization": 23.0, "resets_at": FIVE_HOUR_RESET},
    "seven_day": {"utilization": 41.0, "resets_at": SEVEN_DAY_RESET},
    "seven_day_opus": None,
    "limits": [{"kind": "session", "percent": 23}],
    "cedar_ember": {**stock(grant(1)), "event_props": {"tier": "claude_max_5x"}},
}
PAYLOAD = {
    "ok": True,
    "updatedAt": int(NOW),
    "fiveHour": {"remainingPercent": 77.0, "resetsAt": FIVE_HOUR_RESET_AT},
    "sevenDay": {"remainingPercent": 59.0, "resetsAt": SEVEN_DAY_RESET_AT},
    "resetCreditsAvailable": 1,
    "nextResetCreditExpiresAt": GRANT_END_AT,
}


def present(raw, now=NOW):
    return CLAUDE_USAGE.as_of(CLAUDE_USAGE.usage_payload(raw, NOW), now)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def credentials(token=TOKEN, expires_at=(NOW + 3600) * 1000):
    oauth = {"accessToken": token}
    if expires_at is not None:
        oauth["expiresAt"] = expires_at
    return {"claudeAiOauth": oauth}


def http_error(code, headers=None):
    return urllib.error.HTTPError(
        CLAUDE_USAGE.USAGE_URL, code, "error", headers or {}, io.BytesIO()
    )


def response(body):
    opened = mock.MagicMock()
    opened.__enter__.return_value = io.BytesIO(body)
    return opened


class ParseTest(unittest.TestCase):
    def test_reads_both_windows_and_the_reset_stock(self) -> None:
        self.assertEqual(present(USAGE), PAYLOAD)

    def test_clamps_usage(self) -> None:
        self.assertEqual(
            CLAUDE_USAGE.parse_window({"utilization": 141, "resets_at": None}),
            {"remainingPercent": 0.0, "resetsAt": None},
        )
        self.assertEqual(
            CLAUDE_USAGE.parse_window({"utilization": -5})["remainingPercent"], 100.0
        )

    def test_rejects_invalid_windows(self) -> None:
        for raw in (
            None,
            {},
            {"utilization": True},
            {"utilization": "5"},
            {"utilization": float("nan")},
            {"utilization": float("inf")},
            {"utilization": HUGE},
        ):
            with self.subTest(raw):
                self.assertIsNone(CLAUDE_USAGE.parse_window(raw))

    def test_ignores_unusable_reset_times(self) -> None:
        for value in (None, 1_800_000_000, "tomorrow", "2027-01-15T13:00:00"):
            with self.subTest(value):
                self.assertIsNone(CLAUDE_USAGE.parse_reset(value))

    def test_keeps_a_valid_window_beside_an_invalid_one(self) -> None:
        payload = CLAUDE_USAGE.usage_payload(
            {"five_hour": None, "seven_day": {"utilization": 10, "resets_at": SEVEN_DAY_RESET}},
            NOW,
        )
        self.assertIsNone(payload["fiveHour"])
        self.assertEqual(payload["sevenDay"]["remainingPercent"], 90.0)

    def test_rejects_responses_without_limits(self) -> None:
        for raw in (None, [], {}, {"five_hour": None, "seven_day": {"utilization": None}}):
            with self.subTest(raw), self.assertRaisesRegex(RuntimeError, "Aucune limite"):
                CLAUDE_USAGE.usage_payload(raw, NOW)

    def test_shows_a_window_past_its_reset_as_unused(self) -> None:
        self.assertEqual(
            present(USAGE, FIVE_HOUR_RESET_AT),
            {**PAYLOAD, "fiveHour": {"remainingPercent": 100.0, "resetsAt": None}},
        )
        self.assertEqual(present(USAGE, FIVE_HOUR_RESET_AT - 1), PAYLOAD)

    def test_sums_the_stock_and_shows_the_nearest_expiry(self) -> None:
        earlier = "2027-01-21T08:00:00+00:00"
        payload = present({**USAGE, "cedar_ember": stock(grant(2), grant(1, earlier), grant(1, None))})

        self.assertEqual(payload["resetCreditsAvailable"], 4)
        self.assertEqual(payload["nextResetCreditExpiresAt"], GRANT_END_AT - 86400)

    def test_drops_grants_once_they_expire(self) -> None:
        raw = {**USAGE, "cedar_ember": stock(grant(1, "2027-01-21T08:00:00+00:00"), grant(2))}

        payload = present(raw, GRANT_END_AT - 86400)
        self.assertEqual(payload["resetCreditsAvailable"], 2)
        self.assertEqual(payload["nextResetCreditExpiresAt"], GRANT_END_AT)

        payload = present(raw, GRANT_END_AT)
        self.assertEqual(payload["resetCreditsAvailable"], 0)
        self.assertIsNone(payload["nextResetCreditExpiresAt"])

    def test_skips_used_grants(self) -> None:
        payload = present({**USAGE, "cedar_ember": stock(grant(0, "2027-01-16T08:00:00+00:00"), grant(1))})
        self.assertEqual(payload["resetCreditsAvailable"], 1)
        self.assertEqual(payload["nextResetCreditExpiresAt"], GRANT_END_AT)

    def test_counts_a_grant_only_once_it_starts(self) -> None:
        later = {**grant(1), "starts_at": "2027-01-16T08:00:00+00:00"}
        raw = {**USAGE, "cedar_ember": stock(later)}

        self.assertEqual(present(raw, NOW)["resetCreditsAvailable"], 0)
        self.assertEqual(present(raw, NOW + 86400)["resetCreditsAvailable"], 1)

    def test_treats_an_unreadable_grant_as_an_unknown_stock(self) -> None:
        for bad in (
            None,
            grant(True),
            grant("1"),
            grant(1.0),
            grant(-1),
            grant(1, "soon"),
            grant(1, "2027-01-22T08:00:00"),
            {**grant(1), "starts_at": 5},
        ):
            with self.subTest(bad):
                payload = present({**USAGE, "cedar_ember": stock(grant(1), bad)})
                self.assertIsNone(payload["resetCreditsAvailable"])
                self.assertEqual(payload["fiveHour"], PAYLOAD["fiveHour"])

    def test_leaves_an_unknown_stock_unknown(self) -> None:
        for block in (None, [], {}, stock(grant(1), eligible=False), {"eligible": True, "grants": None}):
            with self.subTest(block):
                payload = present({**USAGE, "cedar_ember": block})
                self.assertIsNone(payload["resetCreditsAvailable"])
                self.assertIsNone(payload["nextResetCreditExpiresAt"])


class AccessTokenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / ".credentials.json"

    def test_reads_a_valid_token(self) -> None:
        write_json(self.path, credentials())
        self.assertEqual(CLAUDE_USAGE.access_token(self.path, NOW), TOKEN)

    def test_accepts_a_token_without_expiry(self) -> None:
        write_json(self.path, credentials(expires_at=None))
        self.assertEqual(CLAUDE_USAGE.access_token(self.path, NOW), TOKEN)

    def test_reports_a_missing_login(self) -> None:
        with self.assertRaisesRegex(RuntimeError, CLAUDE_USAGE.SIGNED_OUT):
            CLAUDE_USAGE.access_token(self.path, NOW)
        for value in ({}, {"claudeAiOauth": None}, credentials(token=""), []):
            with self.subTest(value), self.assertRaisesRegex(RuntimeError, CLAUDE_USAGE.SIGNED_OUT):
                CLAUDE_USAGE.access_token(write_json(self.path, value), NOW)

    def test_reports_unreadable_credentials(self) -> None:
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "illisibles"):
            CLAUDE_USAGE.access_token(self.path, NOW)
        for token in ("two words", "line\nbreak", "jeton-é"):
            with self.subTest(token), self.assertRaisesRegex(RuntimeError, "illisibles"):
                CLAUDE_USAGE.access_token(write_json(self.path, credentials(token=token)), NOW)

    def test_reports_an_expired_token(self) -> None:
        write_json(self.path, credentials(expires_at=NOW * 1000))
        with self.assertRaisesRegex(RuntimeError, CLAUDE_USAGE.EXPIRED):
            CLAUDE_USAGE.access_token(self.path, NOW)


class FetchTest(unittest.TestCase):
    def fetch(self, **open_):
        with mock.patch.object(CLAUDE_USAGE.OPENER, "open", **open_) as opener:
            return CLAUDE_USAGE.fetch_usage(TOKEN, 5), opener

    def test_sends_the_token_to_the_usage_endpoint(self) -> None:
        usage, opener = self.fetch(return_value=response(json.dumps(USAGE).encode()))

        self.assertEqual(usage, USAGE)
        request = opener.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1"
        )
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(request.get_header("Anthropic-beta"), "oauth-2025-04-20")
        # The reset stock is only returned to Claude Code's client identity.
        self.assertEqual(request.get_header("X-app"), "cli")
        self.assertTrue(request.get_header("User-agent").startswith("claude-cli/"))
        self.assertEqual(opener.call_args.kwargs["timeout"], 5)

    def test_maps_failures_to_messages(self) -> None:
        for error, message in (
            (http_error(401), CLAUDE_USAGE.EXPIRED),
            (http_error(403), "Accès refusé"),
            (http_error(429), "Trop de requêtes"),
            (http_error(302), "erreur HTTP 302"),
            (http_error(500), "erreur HTTP 500"),
            (urllib.error.URLError("offline"), "injoignable"),
            (TimeoutError(), "injoignable"),
            (http.client.IncompleteRead(b""), "injoignable"),
            (ValueError(f"Invalid header value b'Bearer {TOKEN}'"), "illisibles"),
        ):
            with self.subTest(message), self.assertRaises(CLAUDE_USAGE.FetchError) as raised:
                self.fetch(side_effect=error)
            self.assertIn(message, str(raised.exception))
            self.assertNotIn(TOKEN, str(raised.exception))

    def test_reports_an_unexpected_response(self) -> None:
        with self.assertRaisesRegex(CLAUDE_USAGE.FetchError, "inattendue"):
            self.fetch(return_value=response(b"<html>"))

    def test_backs_off_after_rate_limiting(self) -> None:
        for headers, wait in (
            ({}, CLAUDE_USAGE.RATE_LIMITED_BACKOFF_SECONDS),
            ({"Retry-After": "900"}, 900),
            ({"Retry-After": "1"}, CLAUDE_USAGE.RATE_LIMITED_BACKOFF_SECONDS),
            ({"Retry-After": "999999"}, CLAUDE_USAGE.MAX_BACKOFF_SECONDS),
            ({"Retry-After": "7200"}, 7200),
            ({"Retry-After": "soon"}, CLAUDE_USAGE.RATE_LIMITED_BACKOFF_SECONDS),
            ({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, CLAUDE_USAGE.RATE_LIMITED_BACKOFF_SECONDS),
            ({"Retry-After": email.utils.formatdate(time.time() + 7200, usegmt=True)}, 7200),
            ({"Retry-After": "\xb2"}, CLAUDE_USAGE.RATE_LIMITED_BACKOFF_SECONDS),
            ({"Retry-After": "9" * 5000}, CLAUDE_USAGE.RATE_LIMITED_BACKOFF_SECONDS),
        ):
            with self.subTest(headers), self.assertRaises(CLAUDE_USAGE.FetchError) as raised:
                self.fetch(side_effect=http_error(429, headers))
            # An HTTP date is measured from now, to the second.
            self.assertAlmostEqual(raised.exception.retry_after, wait, delta=2)

    def test_refuses_redirects(self) -> None:
        handler = next(
            handler
            for handler in CLAUDE_USAGE.OPENER.handlers
            if isinstance(handler, urllib.request.HTTPRedirectHandler)
        )
        request = urllib.request.Request(
            CLAUDE_USAGE.USAGE_URL, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        self.assertIsNone(
            handler.http_error_302(
                request, io.BytesIO(), 302, "Found", {"location": "https://other.example/usage"}
            )
        )


class ReadLimitsTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.cache = root / "cache" / "usage.json"
        write_json(root / ".credentials.json", credentials())
        environment = mock.patch.dict(
            os.environ, {"CLAUDE_CONFIG_DIR": str(root), "CLAUDE_USAGE_CACHE": str(self.cache)}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def read(self, now=NOW, **fetch):
        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", **fetch) as fetch_usage:
            return CLAUDE_USAGE.read_limits(5, clock=lambda: now), fetch_usage

    def fail(self, now, message="Claude injoignable", retry_after=None):
        error = CLAUDE_USAGE.FetchError(message, *(() if retry_after is None else (retry_after,)))
        return self.read(now=now, side_effect=error)

    def test_fetches_and_stores_the_limits(self) -> None:
        payload, fetch_usage = self.read(return_value=USAGE)

        fetch_usage.assert_called_once_with(TOKEN, 5)
        self.assertEqual(payload, PAYLOAD)
        stored = self.cache.read_text(encoding="utf-8")
        self.assertEqual(set(json.loads(stored)["limits"]), {"five_hour", "seven_day", "cedar_ember"})
        self.assertNotIn(TOKEN, stored)

    def test_reuses_a_recent_result(self) -> None:
        self.read(return_value=USAGE)
        payload, fetch_usage = self.read(now=NOW + 29, return_value={})

        fetch_usage.assert_not_called()
        self.assertEqual(payload, PAYLOAD)

    def test_fetches_again_once_the_result_ages(self) -> None:
        self.read(return_value=USAGE)
        later = {**USAGE, "five_hour": {"utilization": 50, "resets_at": FIVE_HOUR_RESET}}
        payload, fetch_usage = self.read(now=NOW + 30, return_value=later)

        fetch_usage.assert_called_once()
        self.assertEqual(payload["fiveHour"]["remainingPercent"], 50.0)
        self.assertEqual(payload["updatedAt"], int(NOW + 30))

    def test_fetches_when_the_clock_moved_back(self) -> None:
        self.read(return_value=USAGE)
        _, fetch_usage = self.read(now=NOW - 60, return_value=USAGE)
        fetch_usage.assert_called_once()

    def test_keeps_the_last_limits_when_a_fetch_fails(self) -> None:
        self.read(return_value=USAGE)
        payload, _ = self.fail(NOW + 600)

        self.assertEqual(payload, {**PAYLOAD, "warning": "Claude injoignable"})

    def test_keeps_the_last_limits_after_an_unexpected_failure(self) -> None:
        self.read(return_value=USAGE)
        payload, _ = self.read(now=NOW + 600, side_effect=KeyError("surprise"))

        self.assertEqual(payload, {**PAYLOAD, "warning": CLAUDE_USAGE.UNAVAILABLE})

    def test_waits_after_a_failure(self) -> None:
        self.read(return_value=USAGE)
        self.fail(NOW + 600)
        payload, fetch_usage = self.read(now=NOW + 629, return_value=USAGE)

        fetch_usage.assert_not_called()
        self.assertEqual(payload, {**PAYLOAD, "warning": "Claude injoignable"})

        payload, fetch_usage = self.read(now=NOW + 630, return_value=USAGE)
        fetch_usage.assert_called_once()
        self.assertNotIn("warning", payload)

    def test_honors_the_rate_limit_backoff(self) -> None:
        self.read(return_value=USAGE)
        self.fail(NOW + 600, "Trop de requêtes à Claude", retry_after=300)

        _, fetch_usage = self.read(now=NOW + 899, return_value=USAGE)
        fetch_usage.assert_not_called()
        _, fetch_usage = self.read(now=NOW + 900, return_value=USAGE)
        fetch_usage.assert_called_once()

    def test_keeps_the_last_limits_when_the_response_has_none(self) -> None:
        self.read(return_value=USAGE)
        payload, _ = self.read(now=NOW + 600, return_value={"five_hour": None})

        self.assertEqual(payload["updatedAt"], int(NOW))
        self.assertIn("Aucune limite", payload["warning"])

    def test_waits_after_a_failure_without_stored_limits(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Claude injoignable"):
            self.fail(NOW)
        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", return_value=USAGE) as fetch_usage:
            with self.assertRaisesRegex(RuntimeError, "Claude injoignable"):
                CLAUDE_USAGE.read_limits(5, clock=lambda: NOW + 10)
        fetch_usage.assert_not_called()
        payload, _ = self.read(now=NOW + 30, return_value=USAGE)
        self.assertEqual(payload["updatedAt"], int(NOW + 30))

    def test_reports_a_missing_login_without_calling_the_endpoint(self) -> None:
        (Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".credentials.json").unlink()
        with self.assertRaisesRegex(RuntimeError, CLAUDE_USAGE.SIGNED_OUT):
            self.read(return_value=USAGE)

    def test_shows_stored_windows_past_their_reset_as_unused(self) -> None:
        self.read(return_value=USAGE)
        payload, _ = self.fail(FIVE_HOUR_RESET_AT)

        self.assertEqual(payload["fiveHour"], {"remainingPercent": 100.0, "resetsAt": None})
        self.assertEqual(payload["sevenDay"], PAYLOAD["sevenDay"])

    def test_ignores_a_corrupt_cache(self) -> None:
        self.cache.parent.mkdir()
        for value in (
            "{",
            "[]",
            '{"updatedAt": "x", "limits": {}}',
            '{"updatedAt": 1, "limits": {}, "attemptedAt": null}',
            '{"attemptedAt": "x", "retryAt": 1800000030, "warning": 5}',
        ):
            with self.subTest(value):
                self.cache.write_text(value, encoding="utf-8")
                payload, _ = self.read(return_value=USAGE)
                self.assertEqual(payload["fiveHour"], PAYLOAD["fiveHour"])
                self.cache.write_text(value, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    self.fail(NOW + 5)

    def test_skips_the_endpoint_when_the_attempt_cannot_be_recorded(self) -> None:
        self.cache.mkdir(parents=True)
        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", return_value=USAGE) as fetch_usage:
            with self.assertRaisesRegex(RuntimeError, CLAUDE_USAGE.CACHE_UNAVAILABLE):
                CLAUDE_USAGE.read_limits(5, clock=lambda: NOW)
        fetch_usage.assert_not_called()

    def test_keeps_the_last_limits_when_the_cache_becomes_read_only(self) -> None:
        self.read(return_value=USAGE)
        with mock.patch.object(CLAUDE_USAGE, "save_state", side_effect=OSError):
            payload, fetch_usage = self.read(now=NOW + 600, return_value=USAGE)
        fetch_usage.assert_not_called()
        self.assertEqual(payload, {**PAYLOAD, "warning": CLAUDE_USAGE.CACHE_UNAVAILABLE})

    def test_keeps_the_backoff_when_the_answer_cannot_be_stored(self) -> None:
        real_save = CLAUDE_USAGE.save_state
        saves = []

        def save_once(path, state):
            saves.append(state)
            if len(saves) > 1:
                raise OSError
            real_save(path, state)

        with mock.patch.object(CLAUDE_USAGE, "save_state", side_effect=save_once):
            with self.assertRaises(RuntimeError):
                self.fail(NOW, "Trop de requêtes à Claude", retry_after=300)
        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", return_value=USAGE) as fetch_usage:
            with self.assertRaises(RuntimeError):
                CLAUDE_USAGE.read_limits(5, clock=lambda: NOW + 299)
        fetch_usage.assert_not_called()

    def test_times_the_backoff_from_the_answer(self) -> None:
        clock = [NOW]

        def slow_refusal(*_):
            clock[0] += 15
            raise CLAUDE_USAGE.FetchError("Trop de requêtes à Claude", 300)

        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", side_effect=slow_refusal):
            with self.assertRaises(RuntimeError):
                CLAUDE_USAGE.read_limits(5, clock=lambda: clock[0])
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(stored["retryAt"], NOW + 315)

    def test_presents_the_limits_as_of_the_answer(self) -> None:
        self.read(return_value=USAGE)
        write_json(Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".credentials.json", credentials(expires_at=None))
        clock = [GRANT_END_AT - 5]

        def slow_failure(*_):
            clock[0] += 15
            raise CLAUDE_USAGE.FetchError("Claude injoignable")

        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", side_effect=slow_failure):
            payload = CLAUDE_USAGE.read_limits(5, clock=lambda: clock[0])
        self.assertEqual(payload["resetCreditsAvailable"], 0)
        self.assertIsNone(payload["nextResetCreditExpiresAt"])

    def test_waits_for_the_helper_holding_the_lock(self) -> None:
        clock = [NOW]
        lock = self.cache.parent / "usage.json.lock"
        lock.parent.mkdir()
        holder = lock.open("a")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX)

        with mock.patch.object(CLAUDE_USAGE, "fetch_usage", return_value=USAGE) as fetch_usage:
            waiter = threading.Thread(
                target=CLAUDE_USAGE.read_limits, args=(5,), kwargs={"clock": lambda: clock[0]}
            )
            waiter.start()
            time.sleep(0.3)
            # The holder fetches at NOW + 1 while the waiter is blocked.
            CLAUDE_USAGE.save_state(
                self.cache,
                {"attemptedAt": NOW + 1, "retryAt": NOW + 31, "updatedAt": NOW + 1, "limits": USAGE},
            )
            clock[0] = NOW + 2
            fcntl.flock(holder, fcntl.LOCK_UN)
            waiter.join(timeout=10)
        fetch_usage.assert_not_called()

    def test_skips_the_endpoint_while_another_helper_holds_the_lock(self) -> None:
        self.read(return_value=USAGE)
        lock = self.cache.with_name("usage.json.lock").open("a")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)

        with mock.patch.object(CLAUDE_USAGE, "LOCK_TIMEOUT_SECONDS", 0.2):
            payload, fetch_usage = self.read(now=NOW + 600, return_value=USAGE)
        fetch_usage.assert_not_called()
        self.assertEqual(payload, PAYLOAD)


class CommandLineTest(unittest.TestCase):
    def run_helper(self, root):
        environment = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(root),
            "CLAUDE_USAGE_CACHE": str(root / "usage.json"),
        }
        result = subprocess.run(
            [sys.executable, str(HELPER_PATH), "--request-id", "1"],
            capture_output=True,
            env=environment,
            text=True,
            timeout=30,
        )
        return result.returncode, json.loads(result.stdout)

    def test_reports_a_missing_login(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, payload = self.run_helper(Path(directory))
        self.assertEqual(code, 1)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["error"], CLAUDE_USAGE.SIGNED_OUT)

    def test_serves_a_recent_result_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = CLAUDE_USAGE.time.time()
            CLAUDE_USAGE.save_state(
                root / "usage.json",
                {"attemptedAt": now, "retryAt": now + 30, "updatedAt": now, "limits": USAGE},
            )
            code, payload = self.run_helper(root)
        self.assertEqual(code, 0)
        self.assertEqual(payload["fiveHour"]["remainingPercent"], 77.0)
        self.assertNotIn("warning", payload)

    def test_never_prints_the_token(self) -> None:
        # A token http.client would reject, and one it rejects anyway.
        for token in ("leak\nme", "leak-me"):
            with self.subTest(token), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_json(root / ".credentials.json", credentials(token=token, expires_at=None))
                output = io.StringIO()
                environment = {"CLAUDE_CONFIG_DIR": str(root), "CLAUDE_USAGE_CACHE": str(root / "usage.json")}
                with mock.patch.dict(os.environ, environment), mock.patch.object(
                    sys, "argv", ["claude_usage.py"]
                ), mock.patch.object(
                    CLAUDE_USAGE.OPENER, "open", side_effect=ValueError(f"Invalid header value b'Bearer {token}'")
                ), contextlib.redirect_stdout(output):
                    code = CLAUDE_USAGE.main()
                self.assertEqual(code, 1)
                self.assertNotIn("leak", output.getvalue())
                self.assertEqual(json.loads(output.getvalue())["error"], CLAUDE_USAGE.UNREADABLE_CREDENTIALS)


if __name__ == "__main__":
    unittest.main()
