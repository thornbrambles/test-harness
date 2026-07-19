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


class RunBuildStageTest(unittest.TestCase):
    def test_no_ready_issue_never_invokes_build_or_verify(self):
        issue_result = SimpleNamespace(stdout="null\n", stderr="")
        build_runner = mock.Mock()
        verify_runner = mock.Mock()

        run.run_build_stage(issue_result, build_runner, verify_runner)

        build_runner.assert_not_called()
        verify_runner.assert_not_called()

    def test_empty_issue_list_output_never_invokes_build_or_verify(self):
        # gh returns an empty string (not the literal "null") when the -q
        # filter finds nothing to index into -- must be guarded too.
        issue_result = SimpleNamespace(stdout="", stderr="")
        build_runner = mock.Mock()
        verify_runner = mock.Mock()

        run.run_build_stage(issue_result, build_runner, verify_runner)

        build_runner.assert_not_called()
        verify_runner.assert_not_called()

    def test_build_success_invokes_verify_with_issue_and_branch(self):
        issue_result = SimpleNamespace(stdout="42\n", stderr="")
        build_runner = mock.Mock(
            return_value=SimpleNamespace(returncode=0, stdout="cloning...\nauto/issue-42\n", stderr="")
        )
        verify_runner = mock.Mock()

        run.run_build_stage(issue_result, build_runner, verify_runner)

        build_runner.assert_called_once_with("42")
        verify_runner.assert_called_once_with("verify.py", "42", "auto/issue-42")

    def test_build_failure_never_invokes_verify(self):
        # build_infra_failure or similar: build.py exits nonzero, so
        # extract_branch() returns None and verify.py must not run.
        issue_result = SimpleNamespace(stdout="42\n", stderr="")
        build_runner = mock.Mock(
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom")
        )
        verify_runner = mock.Mock()

        run.run_build_stage(issue_result, build_runner, verify_runner)

        build_runner.assert_called_once_with("42")
        verify_runner.assert_not_called()

    def test_build_success_but_no_branch_line_never_invokes_verify(self):
        issue_result = SimpleNamespace(stdout="42\n", stderr="")
        build_runner = mock.Mock(
            return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
        )
        verify_runner = mock.Mock()

        run.run_build_stage(issue_result, build_runner, verify_runner)

        verify_runner.assert_not_called()


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
