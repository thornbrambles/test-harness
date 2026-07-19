#!/usr/bin/env python3
"""Tests for tune.py's diff-based PR-open decision (issue #40).

tune.py decides whether to push its branch and open a PR based on whether
`git diff --quiet` against main reports any changes under prompts/ or
config.env (tune.py:34-41). Neither branch of that conditional had a test.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402
import tune  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def _current_branch(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


class TunePrDecisionTest(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.repo_dir, ignore_errors=True))

        _git(self.repo_dir, "init", "-q")
        _git(self.repo_dir, "config", "user.email", "test@example.com")
        _git(self.repo_dir, "config", "user.name", "Test")
        (self.repo_dir / "prompts").mkdir()
        (self.repo_dir / "prompts" / "tuner.md").write_text("base\n", encoding="utf-8")
        (self.repo_dir / "config.env").write_text("A=1\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "base")
        _git(self.repo_dir, "branch", "-M", "main")

        self.prompts_dir_patch = mock.patch.object(tune, "PROMPTS_DIR", self.repo_dir / "prompts")
        self.prompts_dir_patch.start()
        self.addCleanup(self.prompts_dir_patch.stop)

    def _fake_run(self, make_change):
        def _run(cmd, **kwargs):
            if cmd[0] == "git":
                return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
            if cmd[0] == "claude" and make_change:
                # Simulate the Tuner agent editing a tracked prompt file
                # without necessarily committing it -- tune.py's diff check
                # runs against the working tree either way.
                (self.repo_dir / "prompts" / "tuner.md").write_text("changed\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _run

    def _run_tune(self, make_change):
        with mock.patch.object(lib, "run", side_effect=self._fake_run(make_change)), \
             mock.patch.object(lib, "is_halted", return_value=False), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event") as mock_log, \
             mock.patch.object(sys, "argv", ["tune.py"]):
            rc = tune.main()
        return rc, mock_log

    def test_no_changes_skips_pr(self):
        rc, mock_log = self._run_tune(make_change=False)

        self.assertEqual(rc, 0)
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("tune_no_change", logged_events)
        self.assertNotIn("tune_pr_opened", logged_events)
        self.assertEqual(_current_branch(self.repo_dir), "main")

    def test_changes_open_pr(self):
        rc, mock_log = self._run_tune(make_change=True)

        self.assertEqual(rc, 0)
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("tune_pr_opened", logged_events)
        self.assertNotIn("tune_no_change", logged_events)
        self.assertEqual(_current_branch(self.repo_dir), "main")


if __name__ == "__main__":
    unittest.main()
