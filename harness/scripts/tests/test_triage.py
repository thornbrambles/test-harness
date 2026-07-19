#!/usr/bin/env python3
"""Smoke test for triage.py's claude-CLI invocation (issue #40).

Same rationale as test_scan.py: triage.py is a thin claude-CLI wrapper, so
the one thing worth pinning down is that it hands the Triager prompt and
the expected --allowedTools argv to lib.run.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402
import triage  # noqa: E402


class TriageClaudeInvocationTest(unittest.TestCase):
    def test_passes_triager_prompt_and_allowed_tools(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(lib, "run", side_effect=fake_run), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(lib, "is_halted", return_value=False):
            rc = triage.main()

        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        claude_cmd = calls[0]
        self.assertEqual(claude_cmd[0], "claude")
        self.assertEqual(claude_cmd[1], "-p")
        expected_prompt = triage.PROMPTS_DIR.joinpath("triager.md").read_text(encoding="utf-8")
        self.assertEqual(claude_cmd[2], expected_prompt)
        self.assertEqual(claude_cmd[3:], ["--allowedTools", "Bash(gh:*),Read,Grep,Glob"])

    def test_halted_skips_claude_call(self):
        fake_halt_file = mock.Mock()
        fake_halt_file.read_text.return_value = "halted for testing"

        with mock.patch.object(lib, "run") as mock_run, \
             mock.patch.object(lib, "is_halted", return_value=True), \
             mock.patch.object(lib, "HALT_FILE", fake_halt_file):
            rc = triage.main()

        self.assertEqual(rc, 0)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
