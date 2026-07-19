#!/usr/bin/env python3
"""Tests for run.py's branch-extraction and per-stage cadence logic
(issue #40, extended when run.py moved off a shared cycle counter to
independent per-stage intervals).

extract_branch() was previously inlined in the daemon's `while True` loop
with no direct test, only indirect coverage from other scripts' tests.
Pulled out so it can be exercised without running the daemon loop itself.

due() replaces the old should_run_tuner()/in-process cycle counter: each
stage's last-run time is persisted in .harness/state.json (lib.get_last_run/
set_last_run) rather than kept in a local variable, so cadence survives a
daemon restart (see issue #37).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402
import run  # noqa: E402


class ExtractBranchTest(unittest.TestCase):
    def test_returns_last_line_on_success(self):
        result = SimpleNamespace(returncode=0, stdout="cloning...\nbuilding...\nauto/issue-42\n")

        self.assertEqual(run.extract_branch(result), "auto/issue-42")

    def test_ignores_extra_output_before_final_line(self):
        # Regression guard for the exact failure mode issue #40 calls out:
        # an extra print added before build.py's final branch line should
        # not change which line gets picked.
        result = SimpleNamespace(returncode=0, stdout="a\nb\nc\nauto/issue-99")

        self.assertEqual(run.extract_branch(result), "auto/issue-99")

    def test_nonzero_returncode_yields_none(self):
        result = SimpleNamespace(returncode=1, stdout="auto/issue-42\n")

        self.assertIsNone(run.extract_branch(result))

    def test_empty_stdout_yields_none(self):
        result = SimpleNamespace(returncode=0, stdout="")

        self.assertIsNone(run.extract_branch(result))

    def test_whitespace_only_stdout_yields_none(self):
        result = SimpleNamespace(returncode=0, stdout="   \n  \n")

        self.assertIsNone(run.extract_branch(result))


class MainConfigReloadTest(unittest.TestCase):
    """Regression guard for issue #82: main() used to call lib.load_config()
    once before `while True:`, so a config.env edit (e.g. a merged Tuner PR)
    was silently ignored for the rest of the daemon's life. It must now be
    re-read at the top of every loop iteration."""

    def test_load_config_called_once_per_iteration(self):
        tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        halt_file = tmpdir / "halt.lock"
        halt_file.write_text("halted for test", encoding="utf-8")

        configs = [
            {"SCAN_INTERVAL_SECONDS": "600", "TRIAGE_INTERVAL_SECONDS": "600",
             "BUILD_INTERVAL_SECONDS": "600", "TUNER_INTERVAL_SECONDS": "600",
             "LOOP_TICK_SECONDS": "5"},
            {"SCAN_INTERVAL_SECONDS": "0", "TRIAGE_INTERVAL_SECONDS": "0",
             "BUILD_INTERVAL_SECONDS": "0", "TUNER_INTERVAL_SECONDS": "0",
             "LOOP_TICK_SECONDS": "9999"},
            {"SCAN_INTERVAL_SECONDS": "0", "TRIAGE_INTERVAL_SECONDS": "0",
             "BUILD_INTERVAL_SECONDS": "0", "TUNER_INTERVAL_SECONDS": "0",
             "LOOP_TICK_SECONDS": "1"},
        ]

        with mock.patch.object(run.lib, "load_config", side_effect=configs) as mock_load, \
             mock.patch.object(run.lib, "is_halted", side_effect=[False, False, False, False, True]), \
             mock.patch.object(run, "run_stage"), \
             mock.patch.object(run.lib, "reset_daily_counters_if_new_day"), \
             mock.patch.object(run.lib, "bump_counter"), \
             mock.patch.object(run, "due", return_value=False) as mock_due, \
             mock.patch.object(run.lib, "HALT_FILE", halt_file), \
             mock.patch.object(run.time, "sleep") as mock_sleep:
            result = run.main()

        self.assertEqual(result, 1)
        # Three iterations reached the top of the loop (two full passes, then
        # the halt on the third) -- config must have been reloaded each time,
        # not just once before the loop.
        self.assertEqual(mock_load.call_count, 3)

        # due() must see each iteration's own freshly-loaded intervals, not a
        # value frozen at process start.
        self.assertEqual(
            mock_due.call_args_list,
            [
                mock.call("scan", 600), mock.call("triage", 600),
                mock.call("build", 600), mock.call("tune", 600),
                mock.call("scan", 0), mock.call("triage", 0),
                mock.call("build", 0), mock.call("tune", 0),
            ],
        )

        # LOOP_TICK_SECONDS must likewise track each iteration's own config.
        self.assertEqual(mock_sleep.call_args_list, [mock.call(5), mock.call(9999)])


class DueTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))
        self.state_file = self.tmpdir / "state.json"
        self.state_file.write_text('{"daily_cycles": 0, "daily_claude_calls": 0, "date": ""}', encoding="utf-8")
        self._patcher = mock.patch.object(lib, "STATE_FILE", self.state_file)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_never_run_is_immediately_due(self):
        self.assertTrue(run.due("scan", 600))

    def test_not_due_right_after_running(self):
        lib.set_last_run("scan")

        self.assertFalse(run.due("scan", 600))

    def test_due_once_interval_elapses(self):
        state = lib._read_state()
        state["last_scan_ts"] = time.time() - 601
        lib._write_state(state)

        self.assertTrue(run.due("scan", 600))

    def test_zero_or_negative_interval_never_due(self):
        # A stage with interval_seconds <= 0 is disabled outright, matching
        # the old should_run_tuner()'s tuner_every=0 short-circuit -- never
        # true, regardless of how long it's been since the last run.
        self.assertFalse(run.due("tune", 0))
        lib.set_last_run("tune")
        self.assertFalse(run.due("tune", 0))

    def test_stages_are_independent(self):
        lib.set_last_run("scan")

        self.assertFalse(run.due("scan", 600))
        self.assertTrue(run.due("triage", 600))


if __name__ == "__main__":
    unittest.main()
