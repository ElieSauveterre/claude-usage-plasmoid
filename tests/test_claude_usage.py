#!/usr/bin/env python3

import fcntl
import importlib.util
import json
import os
import pty
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import termios
import time
import unittest


HELPER_PATH = (
    Path(__file__).parent.parent / "package" / "contents" / "code" / "claude_usage.py"
)
SPEC = importlib.util.spec_from_file_location("claude_usage", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
CLAUDE_USAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLAUDE_USAGE)

NOW = 1_800_000_000.0
FIVE_HOUR_RESET = 1_800_003_600
SEVEN_DAY_RESET = 1_800_086_400
HUGE = 10**400


def limits(**windows):
    return {
        name: {"used_percentage": used, "resets_at": resets_at}
        for name, (used, resets_at) in windows.items()
    }


def merge(sessions, session_id, rate_limits, observed_at, now, marker=1.0):
    return CLAUDE_USAGE.merged_sessions(
        sessions, session_id, rate_limits, marker, observed_at, now
    )


def session(observed_at, marker=1.0, **windows):
    markers = marker if isinstance(marker, list) else [marker]
    expires_at = max(resets_at for _, resets_at in windows.values())
    return {
        "observed_at": observed_at,
        "cost": markers[-1],
        "response_markers": [[cost, expires_at] for cost in markers],
        "windows": {
            name: {"used_percentage": float(used), "resets_at": resets_at}
            for name, (used, resets_at) in windows.items()
        },
    }


class ParseTest(unittest.TestCase):
    def test_rejects_invalid_windows(self) -> None:
        for raw in (
            None,
            {"used_percentage": True, "resets_at": FIVE_HOUR_RESET},
            {"used_percentage": 5},
            {"used_percentage": 5, "resets_at": float("inf")},
            {"used_percentage": float("nan"), "resets_at": FIVE_HOUR_RESET},
            {"used_percentage": HUGE, "resets_at": FIVE_HOUR_RESET},
            {"used_percentage": 5, "resets_at": HUGE},
        ):
            with self.subTest(raw):
                self.assertIsNone(CLAUDE_USAGE.parse_window(raw))

    def test_invalid_window_does_not_discard_a_valid_one(self) -> None:
        self.assertEqual(
            CLAUDE_USAGE.parse_windows(
                {
                    "five_hour": {"used_percentage": 5, "resets_at": HUGE},
                    "seven_day": {"used_percentage": 141, "resets_at": SEVEN_DAY_RESET},
                }
            ),
            {"seven_day": {"used_percentage": 100.0, "resets_at": SEVEN_DAY_RESET}},
        )

    def test_session_requires_an_observation_time_and_a_marker(self) -> None:
        windows = {"seven_day": {"used_percentage": 40, "resets_at": SEVEN_DAY_RESET}}
        valid = [1.0, SEVEN_DAY_RESET]
        for observed_at, markers in (
            (None, [valid]),
            (HUGE, [valid]),
            (-1, [valid]),
            (NOW, None),
            (NOW, []),
            (NOW, [valid, [0, SEVEN_DAY_RESET]]),
            (NOW, [1.0]),
            (NOW, [[1.0, None]]),
        ):
            with self.subTest(observed_at=observed_at, markers=markers):
                self.assertIsNone(
                    CLAUDE_USAGE.parse_session(
                        {"observed_at": observed_at, "cost": 1.0, "response_markers": markers, "windows": windows}
                    )
                )
        self.assertIsNone(
            CLAUDE_USAGE.parse_session(
                {"observed_at": NOW, "response_markers": [valid], "windows": windows}
            )
        )


class MergedSessionsTest(unittest.TestCase):
    def test_records_a_session_with_its_observation_time(self) -> None:
        merged = merge(
            {},
            "a",
            limits(five_hour=(23.5, FIVE_HOUR_RESET), seven_day=(41.2, SEVEN_DAY_RESET)),
            NOW - 0.5,
            NOW,
        )

        self.assertEqual(
            merged,
            {"a": session(NOW - 0.5, five_hour=(23.5, FIVE_HOUR_RESET), seven_day=(41.2, SEVEN_DAY_RESET))},
        )

    def test_ignores_missing_and_expired_limits(self) -> None:
        for rate_limits in (None, {}, limits(five_hour=(5, int(NOW)))):
            with self.subTest(rate_limits):
                self.assertIsNone(merge({}, "a", rate_limits, NOW, NOW))

    def test_changed_limits_without_a_new_response_change_nothing(self) -> None:
        # For example, a resumed session showing limits left in the process.
        stored = {"a": session(NOW - 10800, seven_day=(60, SEVEN_DAY_RESET))}

        self.assertIsNone(
            merge(stored, "a", limits(seven_day=(20, SEVEN_DAY_RESET)), NOW, NOW)
        )

    def test_new_response_takes_the_new_observation_time(self) -> None:
        stored = {"a": session(NOW - 10800, seven_day=(40, SEVEN_DAY_RESET))}

        merged = merge(
            stored, "a", limits(seven_day=(12, SEVEN_DAY_RESET)), NOW, NOW, 2.0
        )

        self.assertEqual(merged, {"a": session(NOW, [1.0, 2.0], seven_day=(12, SEVEN_DAY_RESET))})

    def test_new_response_with_identical_limits_takes_the_new_time(self) -> None:
        stored = {
            "a": session(NOW - 3600, 0.25, seven_day=(40, SEVEN_DAY_RESET)),
            "b": session(NOW - 60, 0.50, seven_day=(12, SEVEN_DAY_RESET)),
        }

        merged = merge(stored, "a", limits(seven_day=(40, SEVEN_DAY_RESET)), NOW, NOW, 0.31)

        self.assertEqual(merged["a"], session(NOW, [0.25, 0.31], seven_day=(40, SEVEN_DAY_RESET)))
        self.assertEqual(
            CLAUDE_USAGE.current_windows(merged, NOW)["seven_day"]["used_percentage"], 40.0
        )

    def test_replay_with_the_same_marker_changes_nothing(self) -> None:
        stored = {"a": session(NOW - 3600, 0.25, seven_day=(40, SEVEN_DAY_RESET))}

        self.assertIsNone(
            merge(stored, "a", limits(seven_day=(40, SEVEN_DAY_RESET)), NOW, NOW, 0.25)
        )

    def test_idle_process_sharing_the_session_cannot_replay_its_old_marker(self) -> None:
        stored = {"s": session(NOW - 3600, 1.0, seven_day=(20, SEVEN_DAY_RESET))}
        stored = merge(stored, "s", limits(seven_day=(70, SEVEN_DAY_RESET)), NOW - 60, NOW, 2.0)

        self.assertIsNone(merge(stored, "s", limits(seven_day=(20, SEVEN_DAY_RESET)), NOW, NOW, 1.0))

    def test_older_response_arriving_late_is_remembered_without_replacing_limits(self) -> None:
        stored = {"s": session(NOW, 2.0, seven_day=(70, SEVEN_DAY_RESET))}

        merged = merge(stored, "s", limits(seven_day=(20, SEVEN_DAY_RESET)), NOW - 10, NOW, 1.5)

        self.assertEqual(merged["s"]["windows"], stored["s"]["windows"])
        self.assertEqual(merged["s"]["observed_at"], NOW)
        self.assertIsNone(merge(merged, "s", limits(seven_day=(20, SEVEN_DAY_RESET)), NOW, NOW, 1.5))

    def test_transcript_time_tie_resolves_to_the_higher_cost(self) -> None:
        newer_first = merge(
            {"s": session(NOW, 2.0, seven_day=(70, SEVEN_DAY_RESET))},
            "s", limits(seven_day=(20, SEVEN_DAY_RESET)), NOW, NOW, 1.5,
        )
        older_first = merge(
            {"s": session(NOW, 1.5, seven_day=(20, SEVEN_DAY_RESET))},
            "s", limits(seven_day=(70, SEVEN_DAY_RESET)), NOW, NOW, 2.0,
        )

        for merged in (newer_first, older_first):
            self.assertEqual(merged["s"]["windows"]["seven_day"]["used_percentage"], 70.0)

    def test_markers_are_kept_while_their_windows_are_live(self) -> None:
        stored = {"s": session(NOW - 3600, 1.0, seven_day=(20, SEVEN_DAY_RESET))}
        for index in range(300):
            stored = merge(stored, "s", limits(seven_day=(21 + index * 0.1, SEVEN_DAY_RESET)), NOW, NOW, 2.0 + index)

        self.assertIsNone(merge(stored, "s", limits(seven_day=(20, SEVEN_DAY_RESET)), NOW, NOW, 1.0))

    def test_markers_are_dropped_once_their_windows_reset(self) -> None:
        stored = {
            "s": {
                "observed_at": NOW - 3600,
                "cost": 2.0,
                "response_markers": [[1.0, int(NOW) - 1], [2.0, SEVEN_DAY_RESET]],
                "windows": {"seven_day": {"used_percentage": 30.0, "resets_at": SEVEN_DAY_RESET}},
            }
        }

        merged = merge(stored, "s", limits(seven_day=(40, SEVEN_DAY_RESET)), NOW, NOW, 3.0)

        self.assertEqual(
            merged["s"]["response_markers"], [[2.0, SEVEN_DAY_RESET], [3.0, SEVEN_DAY_RESET]]
        )

    def test_dropped_expired_window_is_not_a_change(self) -> None:
        stored = {
            "a": session(
                NOW - 7200, five_hour=(90, int(NOW) - 10), seven_day=(10, SEVEN_DAY_RESET)
            )
        }

        self.assertIsNone(
            merge(
                stored, "a", limits(seven_day=(10, SEVEN_DAY_RESET)), NOW, NOW
            )
        )
        self.assertEqual(
            CLAUDE_USAGE.widget_window(
                CLAUDE_USAGE.current_windows(stored, NOW)["five_hour"], NOW
            )["remainingPercent"],
            100.0,
        )

    def test_session_older_than_every_kept_session_is_not_saved(self) -> None:
        stored = {
            str(index): session(NOW - index, seven_day=(index, SEVEN_DAY_RESET))
            for index in range(CLAUDE_USAGE.MAX_SESSIONS)
        }

        self.assertIsNone(
            merge(
                stored, "old", limits(seven_day=(50, SEVEN_DAY_RESET)), NOW - 3600, NOW
            )
        )

    def test_idle_session_is_remembered_while_its_windows_are_valid(self) -> None:
        stored = {"idle": session(NOW - 86400, seven_day=(10, SEVEN_DAY_RESET))}
        for index in range(40):
            stored = merge(
                stored, str(index), limits(seven_day=(20 + index, SEVEN_DAY_RESET)), NOW - 40 + index, NOW
            )

        self.assertIn("idle", stored)
        self.assertIsNone(
            merge(
                stored, "idle", limits(seven_day=(10, SEVEN_DAY_RESET)), NOW, NOW
            )
        )

    def test_forgets_sessions_a_week_after_their_windows_reset(self) -> None:
        week = CLAUDE_USAGE.PRUNE_AFTER_SECONDS
        stored = {
            "recent_reset": session(NOW - week, seven_day=(10, int(NOW) - week + 60)),
            "old_reset": session(NOW - week, seven_day=(10, int(NOW) - week - 60)),
        }

        merged = merge(
            stored, "new", limits(seven_day=(50, SEVEN_DAY_RESET)), NOW, NOW
        )

        self.assertEqual(set(merged), {"recent_reset", "new"})

    def test_cap_evicts_sessions_whose_windows_have_reset_first(self) -> None:
        stored = {
            str(index): session(NOW - 10 - index, seven_day=(index, SEVEN_DAY_RESET))
            for index in range(CLAUDE_USAGE.MAX_SESSIONS - 2)
        }
        stored["live_but_old"] = session(NOW - 100000, seven_day=(5, SEVEN_DAY_RESET))
        stored["reset_but_recent"] = session(NOW - 1, five_hour=(90, int(NOW) - 60))

        merged = merge(stored, "new", limits(seven_day=(50, SEVEN_DAY_RESET)), NOW, NOW)

        self.assertIn("live_but_old", merged)
        self.assertNotIn("reset_but_recent", merged)

    def test_keeps_the_most_recent_sessions(self) -> None:
        stored = {
            str(index): session(NOW - 1000 + index, seven_day=(index, SEVEN_DAY_RESET))
            for index in range(CLAUDE_USAGE.MAX_SESSIONS)
        }

        merged = merge(
            stored, "new", limits(seven_day=(50, SEVEN_DAY_RESET)), NOW, NOW
        )

        self.assertEqual(len(merged), CLAUDE_USAGE.MAX_SESSIONS)
        self.assertIn("new", merged)
        self.assertNotIn("0", merged)


class CurrentWindowsTest(unittest.TestCase):
    def test_most_recent_observation_wins_by_subsecond_order(self) -> None:
        sessions = {
            "newer": session(NOW + 0.9, seven_day=(55, SEVEN_DAY_RESET)),
            "older": session(NOW + 0.1, seven_day=(40, SEVEN_DAY_RESET)),
        }

        current = CLAUDE_USAGE.current_windows(sessions, NOW + 1)

        self.assertEqual(current["seven_day"]["used_percentage"], 55.0)

    def test_newer_lower_usage_wins(self) -> None:
        sessions = {
            "before_upgrade": session(NOW - 60, seven_day=(40, SEVEN_DAY_RESET)),
            "after_upgrade": session(NOW, seven_day=(12, SEVEN_DAY_RESET)),
        }

        self.assertEqual(
            CLAUDE_USAGE.current_windows(sessions, NOW)["seven_day"]["used_percentage"], 12.0
        )

    def test_tie_resolves_to_higher_usage(self) -> None:
        sessions = {
            "a": session(NOW, seven_day=(40, SEVEN_DAY_RESET)),
            "b": session(NOW, seven_day=(41, SEVEN_DAY_RESET)),
        }

        self.assertEqual(
            CLAUDE_USAGE.current_windows(sessions, NOW)["seven_day"]["used_percentage"], 41.0
        )

    def test_window_that_has_not_reset_beats_a_newer_elapsed_one(self) -> None:
        sessions = {
            "elapsed": session(NOW - 10, five_hour=(90, int(NOW) - 5)),
            "current": session(NOW - 100, five_hour=(3, FIVE_HOUR_RESET)),
        }

        self.assertEqual(
            CLAUDE_USAGE.current_windows(sessions, NOW)["five_hour"]["used_percentage"], 3.0
        )


class ReadLimitsTest(unittest.TestCase):
    def write(self, directory: str, snapshot) -> Path:
        path = Path(directory) / "rate_limits.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path

    def test_reports_remaining_and_least_recent_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                {
                    "sessions": {
                        "a": session(NOW - 60, five_hour=(130, FIVE_HOUR_RESET)),
                        "b": session(NOW - 86400, seven_day=(40, SEVEN_DAY_RESET)),
                    }
                },
            )

            result = CLAUDE_USAGE.read_limits(path, NOW)

        self.assertEqual(result["fiveHour"]["remainingPercent"], 0.0)
        self.assertEqual(result["sevenDay"]["remainingPercent"], 60.0)
        self.assertEqual(result["updatedAt"], NOW - 86400)

    def test_elapsed_window_is_fully_available_as_of_its_reset(self) -> None:
        window = {"used_percentage": 80.0, "resets_at": int(NOW) - 30, "observed_at": NOW - 7200}

        self.assertEqual(
            CLAUDE_USAGE.widget_window(window, NOW),
            {"remainingPercent": 100.0, "resetsAt": None, "observedAt": int(NOW) - 30},
        )

    def test_malformed_snapshot_keeps_valid_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                {
                    "sessions": {
                        "bad": {"observed_at": HUGE, "windows": limits(seven_day=(1, SEVEN_DAY_RESET))},
                        "good": session(NOW, seven_day=(40, SEVEN_DAY_RESET)),
                    }
                },
            )

            self.assertEqual(
                CLAUDE_USAGE.load_sessions(path),
                {"good": session(NOW, seven_day=(40, SEVEN_DAY_RESET))},
            )

    def test_malformed_snapshot_reads_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"sessions": ["bad"]})

            self.assertEqual(CLAUDE_USAGE.load_sessions(path), {})
            with self.assertRaisesRegex(RuntimeError, "status line"):
                CLAUDE_USAGE.read_limits(path, NOW)


class CommandLineTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.cache = self.directory / "nested" / "rate_limits.json"

    def transcript(self, name: str, modified: float) -> str:
        path = self.directory / name
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (modified, modified))
        return str(path)

    def run_helper(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(HELPER_PATH), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_USAGE_CACHE": str(self.cache)},
            check=False,
        )

    def record(self, session_id, transcript, cost=1.0, **windows) -> subprocess.CompletedProcess:
        payload = {"rate_limits": limits(**windows)}
        if cost is not None:
            payload["cost"] = {"total_cost_usd": cost}
        if session_id is not None:
            payload["session_id"] = session_id
        if transcript is not None:
            payload["transcript_path"] = transcript
        return self.run_helper("record", stdin=json.dumps(payload))

    def test_record_then_read_round_trip(self) -> None:
        recorded = self.record(
            "a",
            self.transcript("a.jsonl", time.time()),
            five_hour=(37.5, 4_000_000_000),
            seven_day=(40, 4_000_100_000),
        )
        self.assertEqual(recorded.returncode, 0)
        self.assertEqual(recorded.stdout.strip(), "Claude 5h 63% · 7j 60% restant")

        result = json.loads(self.run_helper("--request-id", "1").stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["fiveHour"]["remainingPercent"], 62.5)
        self.assertEqual(result["sevenDay"]["resetsAt"], 4_000_100_000)

    def test_idle_session_replay_after_a_transcript_write_changes_nothing(self) -> None:
        idle = self.transcript("idle.jsonl", time.time() - 7200)
        self.record("idle", idle, 0.4, seven_day=(10, 4_000_100_000))
        self.record("active", self.transcript("active.jsonl", time.time() - 60), 1.2, seven_day=(70, 4_000_100_000))

        # A local command such as /usage touches the idle transcript without an API call.
        os.utime(idle)
        replay = self.record("idle", idle, 0.4, seven_day=(10, 4_000_100_000))

        self.assertEqual(replay.stdout.strip(), "Claude 7j 30% restant")

    def test_new_response_with_identical_limits_wins(self) -> None:
        a = self.transcript("a.jsonl", time.time() - 3600)
        self.record("a", a, 0.4, seven_day=(40, 4_000_100_000))
        self.record("b", self.transcript("b.jsonl", time.time() - 60), 0.9, seven_day=(12, 4_000_100_000))

        os.utime(a)
        latest = self.record("a", a, 0.5, seven_day=(40, 4_000_100_000))

        self.assertEqual(latest.stdout.strip(), "Claude 7j 60% restant")

    def test_observations_within_one_second_keep_their_order(self) -> None:
        second = int(time.time()) - 10
        self.record("newer", self.transcript("newer.jsonl", second + 0.9), seven_day=(40, 4_000_100_000))
        replay = self.record("older", self.transcript("older.jsonl", second + 0.1), seven_day=(55, 4_000_100_000))

        self.assertEqual(replay.stdout.strip(), "Claude 7j 60% restant")

    def test_session_without_a_response_cost_is_not_recorded(self) -> None:
        transcript = self.transcript("cleared.jsonl", time.time())
        for cost in (0, None):
            with self.subTest(cost=cost):
                recorded = self.record("cleared", transcript, cost, five_hour=(25, 4_000_000_000))

                self.assertEqual(recorded.stdout.strip(), "Claude")
                self.assertFalse(self.cache.exists())

    def test_record_requires_a_session_and_a_transcript(self) -> None:
        transcript = self.transcript("a.jsonl", time.time())
        for session_id, path in ((None, transcript), ("a", None), ("a", str(self.directory / "missing"))):
            with self.subTest(session_id=session_id, transcript=path):
                recorded = self.record(session_id, path, five_hour=(25, 4_000_000_000))

                self.assertEqual(recorded.stdout.strip(), "Claude")
                self.assertFalse(self.cache.parent.exists())

    def test_record_forwards_stdin_to_wrapped_command(self) -> None:
        recorded = self.run_helper("record", "--", "cat", stdin="not json é")

        self.assertEqual(recorded.returncode, 0)
        self.assertEqual(recorded.stdout, "not json é")
        self.assertFalse(self.cache.exists())

    def test_record_survives_bad_cache_and_failing_command(self) -> None:
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text('{"sessions":{"a":{"observed_at":1e999}}}', encoding="utf-8")
        for command in (
            ["/nonexistent/statusline"],
            [sys.executable, "-c", "print('broken'); raise SystemExit(1)"],
        ):
            with self.subTest(command):
                recorded = self.run_helper("record", "--", *command, stdin="[]")

                self.assertEqual(recorded.returncode, 0)
                self.assertEqual(recorded.stdout.strip(), "Claude")

    def test_wrapped_command_keeps_the_controlling_terminal(self) -> None:
        master, terminal = pty.openpty()
        self.addCleanup(os.close, master)

        def take_terminal() -> None:
            os.setsid()
            fcntl.ioctl(terminal, termios.TIOCSCTTY, 0)

        recorded = subprocess.run(
            [sys.executable, str(HELPER_PATH), "record", "--", "/bin/sh", "-c", "stty -echo </dev/tty && echo tty"],
            input=b"{}",
            capture_output=True,
            env={**os.environ, "CLAUDE_USAGE_CACHE": str(self.cache)},
            preexec_fn=take_terminal,
            pass_fds=(terminal,),
            timeout=10,
            check=False,
        )
        os.close(terminal)

        self.assertEqual(recorded.stdout.strip(), b"tty")

    def test_cancelling_while_the_wrapped_command_launches_stops_it(self) -> None:
        finished = self.directory / "finished"
        # Deliver SIGTERM after the child exists but before Popen returns.
        script = f"""
import importlib.util, os, signal, subprocess, sys
spec = importlib.util.spec_from_file_location("claude_usage", {str(HELPER_PATH)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
execute_child = subprocess.Popen._execute_child
def execute_then_cancel(self, *args, **kwargs):
    execute_child(self, *args, **kwargs)
    os.kill(os.getpid(), signal.SIGTERM)
subprocess.Popen._execute_child = execute_then_cancel
module.run_wrapped(["/bin/sh", "-c", "sleep 1; touch {finished}"], b"")
"""
        cancelled = subprocess.run([sys.executable, "-c", script], check=False)
        time.sleep(1.5)

        self.assertEqual(cancelled.returncode, 128 + signal.SIGTERM)
        self.assertFalse(finished.exists())

    def test_cancelling_the_recorder_stops_the_wrapped_command(self) -> None:
        started, finished = self.directory / "started", self.directory / "finished"
        child = (
            "import pathlib, sys, time; pathlib.Path(sys.argv[1]).touch(); "
            "time.sleep(1.5); pathlib.Path(sys.argv[2]).touch()"
        )
        recorder = subprocess.Popen(
            [sys.executable, str(HELPER_PATH), "record", "--", sys.executable, "-c", child, str(started), str(finished)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            env={**os.environ, "CLAUDE_USAGE_CACHE": str(self.cache)},
        )
        recorder.stdin.close()
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)

        recorder.terminate()
        recorder.wait(timeout=5)
        time.sleep(2)

        self.assertTrue(started.exists())
        self.assertFalse(finished.exists())

    def test_record_skips_the_update_while_another_session_holds_the_lock(self) -> None:
        transcript = self.transcript("a.jsonl", time.time())

        with CLAUDE_USAGE.locked(self.cache):
            recorded = self.record("a", transcript, five_hour=(25, 4_000_000_000))

        self.assertEqual(recorded.returncode, 0)
        self.assertEqual(recorded.stdout.strip(), "Claude")
        self.assertFalse(self.cache.exists())

    def test_read_without_snapshot_explains_setup(self) -> None:
        read = self.run_helper()

        self.assertEqual(read.returncode, 1)
        self.assertIn("status line", json.loads(read.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
