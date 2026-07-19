#!/usr/bin/env python3
"""Tests for overseer.py's circuit breakers (issue #9).

check_reject_rate and check_human_reopen are the daemon's two circuit
breakers -- neither had a regression test. check_human_reopen in particular
was a documented no-op in the old bash version (see overseer.py:44-48) and
is easy to silently re-break, so its "reopened issue halts the daemon"
behavior gets an explicit test here.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402
import overseer  # noqa: E402


def _write_log(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class RejectRateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))
        self.log_file = self.tmpdir / "log.jsonl"
        self._patcher = mock.patch.object(lib, "LOG_FILE", self.log_file)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)


class CheckRejectRateTest(RejectRateTestBase):
    CONFIG = {"REJECT_RATE_WINDOW": "10", "MAX_REJECT_RATE": "0.5"}

    def test_not_enough_verdicts_yet_passes(self):
        _write_log(self.log_file, [{"event": "verify_reject_final", "issue": "1"}] * 9)

        ok, reason = overseer.check_reject_rate(self.CONFIG)

        self.assertTrue(ok, reason)

    def test_high_reject_rate_halts(self):
        entries = [{"event": "verify_reject_final", "issue": "1"}] * 7
        entries += [{"event": "verify_approve", "issue": "1"}] * 3
        _write_log(self.log_file, entries)

        ok, reason = overseer.check_reject_rate(self.CONFIG)

        self.assertFalse(ok)
        self.assertIn("reject rate", reason)

    def test_low_reject_rate_passes(self):
        entries = [{"event": "verify_reject_final", "issue": "1"}] * 3
        entries += [{"event": "verify_approve", "issue": "1"}] * 7
        _write_log(self.log_file, entries)

        ok, reason = overseer.check_reject_rate(self.CONFIG)

        self.assertTrue(ok, reason)

    def test_only_last_window_entries_considered(self):
        # 20 rejects outside the window, then 10 approves inside it -- if the
        # window slicing were wrong (e.g. counting the whole log) this would
        # incorrectly halt.
        entries = [{"event": "verify_reject_final", "issue": "1"}] * 20
        entries += [{"event": "verify_approve", "issue": "1"}] * 10
        _write_log(self.log_file, entries)

        ok, reason = overseer.check_reject_rate(self.CONFIG)

        self.assertTrue(ok, reason)

    def test_unrelated_events_are_ignored(self):
        entries = [{"event": "gate_pass", "issue": "1"}] * 50
        entries += [{"event": "verify_reject_final", "issue": "1"}] * 3
        entries += [{"event": "verify_approve", "issue": "1"}] * 6
        _write_log(self.log_file, entries)

        ok, reason = overseer.check_reject_rate(self.CONFIG)

        # Only 9 relevant verdicts logged (< window of 10) -> not enough data.
        self.assertTrue(ok, reason)


class CheckHumanReopenTest(RejectRateTestBase):
    def _fake_run(self, states: dict[str, str]):
        def fake(cmd, **kwargs):
            issue = cmd[3]
            return SimpleNamespace(returncode=0, stdout=states.get(issue, "CLOSED") + "\n", stderr="")
        return fake

    def test_no_auto_closed_issues_passes_without_gh_calls(self):
        _write_log(self.log_file, [{"event": "gate_pass", "issue": "1"}])

        with mock.patch.object(lib, "run") as mock_run:
            ok, reason = overseer.check_human_reopen()

        self.assertTrue(ok, reason)
        mock_run.assert_not_called()

    def test_still_closed_issue_passes(self):
        _write_log(self.log_file, [{"event": "verify_approve", "issue": "5"}])

        with mock.patch.object(lib, "run", side_effect=self._fake_run({"5": "CLOSED"})):
            ok, reason = overseer.check_human_reopen()

        self.assertTrue(ok, reason)

    def test_reopened_issue_halts(self):
        _write_log(self.log_file, [{"event": "verify_approve", "issue": "5"}])

        with mock.patch.object(lib, "run", side_effect=self._fake_run({"5": "OPEN"})):
            ok, reason = overseer.check_human_reopen()

        self.assertFalse(ok)
        self.assertIn("#5", reason)
        self.assertIn("reopened", reason)

    def test_placeholder_issue_dash_is_skipped(self):
        _write_log(self.log_file, [{"event": "verify_approve", "issue": "-"}])

        with mock.patch.object(lib, "run") as mock_run:
            ok, reason = overseer.check_human_reopen()

        self.assertTrue(ok, reason)
        mock_run.assert_not_called()


class MainTest(unittest.TestCase):
    """overseer.main() ties the two circuit breakers plus the daily-cap
    short-circuits together; this exercises the ordering directly (issue
    #85) since none of it was covered before -- including that a failing
    earlier check must prevent later checks from ever running."""

    CONFIG = {
        "MAX_DAILY_CYCLES": "50",
        "MAX_DAILY_CLAUDE_CALLS": "200",
        "HALT_ON_HUMAN_REOPEN": "true",
    }

    def setUp(self):
        patchers = [
            mock.patch.object(lib, "load_config", return_value=dict(self.CONFIG)),
            mock.patch.object(lib, "reset_daily_counters_if_new_day"),
            mock.patch.object(lib, "halt_daemon"),
            mock.patch.object(lib, "log_event"),
            mock.patch.object(overseer, "check_reject_rate", return_value=(True, "")),
            mock.patch.object(overseer, "check_human_reopen", return_value=(True, "")),
        ]
        self.mocks = {}
        for patcher in patchers:
            name = patcher.attribute
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def _set_counters(self, daily_cycles=0, daily_claude_calls=0):
        def fake(key):
            return {"daily_cycles": daily_cycles, "daily_claude_calls": daily_claude_calls}[key]
        patcher = mock.patch.object(lib, "get_counter", side_effect=fake)
        self.mocks["get_counter"] = patcher.start()
        self.addCleanup(patcher.stop)

    def test_daily_cycle_cap_halts_before_other_checks(self):
        self._set_counters(daily_cycles=50, daily_claude_calls=0)

        rc = overseer.main()

        self.assertEqual(rc, 0)
        self.mocks["halt_daemon"].assert_called_once()
        self.assertIn("cycle cap", self.mocks["halt_daemon"].call_args[0][0])
        overseer.check_reject_rate.assert_not_called()
        overseer.check_human_reopen.assert_not_called()
        lib.log_event.assert_not_called()

    def test_daily_claude_call_cap_halts_before_reject_rate_check(self):
        self._set_counters(daily_cycles=0, daily_claude_calls=200)

        rc = overseer.main()

        self.assertEqual(rc, 0)
        self.mocks["halt_daemon"].assert_called_once()
        self.assertIn("claude call cap", self.mocks["halt_daemon"].call_args[0][0])
        overseer.check_reject_rate.assert_not_called()
        overseer.check_human_reopen.assert_not_called()

    def test_failing_reject_rate_halts_before_human_reopen_check(self):
        self._set_counters()
        overseer.check_reject_rate.return_value = (False, "reject rate too high")

        rc = overseer.main()

        self.assertEqual(rc, 0)
        self.mocks["halt_daemon"].assert_called_once_with("reject rate too high")
        overseer.check_human_reopen.assert_not_called()
        lib.log_event.assert_not_called()

    def test_human_reopen_only_checked_when_config_enabled(self):
        self._set_counters()
        config = dict(self.CONFIG)
        config["HALT_ON_HUMAN_REOPEN"] = "false"
        lib.load_config.return_value = config

        rc = overseer.main()

        self.assertEqual(rc, 0)
        overseer.check_human_reopen.assert_not_called()
        self.mocks["halt_daemon"].assert_not_called()
        lib.log_event.assert_called_once_with("overseer_check", "-", {})

    def test_failing_human_reopen_check_halts(self):
        self._set_counters()
        overseer.check_human_reopen.return_value = (False, "issue #5 was auto-closed but is now reopened")

        rc = overseer.main()

        self.assertEqual(rc, 0)
        self.mocks["halt_daemon"].assert_called_once_with("issue #5 was auto-closed but is now reopened")
        lib.log_event.assert_not_called()

    def test_all_checks_pass_logs_ok_and_returns_zero(self):
        self._set_counters()

        rc = overseer.main()

        self.assertEqual(rc, 0)
        self.mocks["halt_daemon"].assert_not_called()
        lib.log_event.assert_called_once_with("overseer_check", "-", {})


if __name__ == "__main__":
    unittest.main()
