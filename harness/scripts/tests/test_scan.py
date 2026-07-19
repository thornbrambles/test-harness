#!/usr/bin/env python3
"""Smoke tests for scan.py's claude-CLI invocation (issue #40).

scan.py is a thin wrapper around the `claude` CLI with little pure logic of
its own -- these assert it hands the Scanner prompt (with placeholders
substituted) and the expected --allowedTools argv to lib.run, since that's
the one thing a change here could quietly break.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402
import scan  # noqa: E402


class ScanClaudeInvocationTest(unittest.TestCase):
    def test_passes_scanner_prompt_and_allowed_tools(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "gh":
                return SimpleNamespace(returncode=0, stdout="3\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        config = {"MAX_ISSUES_PER_SCAN": "8", "MAX_OPEN_AUTO_ISSUES": "50"}

        with mock.patch.object(lib, "run", side_effect=fake_run), \
             mock.patch.object(lib, "load_config", return_value=config), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(lib, "is_halted", return_value=False):
            rc = scan.main()

        self.assertEqual(rc, 0)
        claude_calls = [c for c in calls if c[0] == "claude"]
        self.assertEqual(len(claude_calls), 1)
        claude_cmd = claude_calls[0]
        self.assertEqual(claude_cmd[1], "-p")
        prompt = claude_cmd[2]
        self.assertIn("8", prompt)
        self.assertIn("50", prompt)
        self.assertNotIn("{{MAX_ISSUES_PER_SCAN}}", prompt)
        self.assertNotIn("{{MAX_OPEN_AUTO_ISSUES}}", prompt)
        self.assertEqual(
            claude_cmd[3:],
            ["--allowedTools", "Bash(gh:*),Bash(git:*),Read,Grep,Glob"],
        )

    def test_halted_skips_claude_call(self):
        fake_halt_file = mock.Mock()
        fake_halt_file.read_text.return_value = "halted for testing"

        with mock.patch.object(lib, "run") as mock_run, \
             mock.patch.object(lib, "is_halted", return_value=True), \
             mock.patch.object(lib, "HALT_FILE", fake_halt_file):
            rc = scan.main()

        self.assertEqual(rc, 0)
        mock_run.assert_not_called()


class ScanInfraFailureTest(unittest.TestCase):
    """Tests for issue #51: scan.py used to log scan_complete unconditionally,
    even when the claude CLI crashed or hit an auth/rate-limit error. That
    left no signal in .harness/log.jsonl that the Scanner stage silently
    failed."""

    def _fake_run(self, claude_returncode):
        def _run(cmd, **kwargs):
            if cmd[0] == "gh":
                return SimpleNamespace(returncode=0, stdout="3\n", stderr="")
            if cmd[0] == "claude":
                return SimpleNamespace(returncode=claude_returncode, stdout="", stderr="rate limited")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _run

    def test_nonzero_exit_logs_infra_failure_not_complete(self):
        config = {"MAX_ISSUES_PER_SCAN": "8", "MAX_OPEN_AUTO_ISSUES": "50"}

        with mock.patch.object(lib, "run", side_effect=self._fake_run(1)), \
             mock.patch.object(lib, "load_config", return_value=config), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event") as mock_log, \
             mock.patch.object(lib, "is_halted", return_value=False):
            rc = scan.main()

        self.assertEqual(rc, 1)
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("scan_infra_failure", logged_events)
        self.assertNotIn("scan_complete", logged_events)

    def test_zero_exit_still_logs_complete(self):
        config = {"MAX_ISSUES_PER_SCAN": "8", "MAX_OPEN_AUTO_ISSUES": "50"}

        with mock.patch.object(lib, "run", side_effect=self._fake_run(0)), \
             mock.patch.object(lib, "load_config", return_value=config), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event") as mock_log, \
             mock.patch.object(lib, "is_halted", return_value=False):
            rc = scan.main()

        self.assertEqual(rc, 0)
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("scan_complete", logged_events)
        self.assertNotIn("scan_infra_failure", logged_events)


if __name__ == "__main__":
    unittest.main()
