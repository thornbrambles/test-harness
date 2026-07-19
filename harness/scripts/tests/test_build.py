#!/usr/bin/env python3
"""Tests for build.py leaving HEAD on main when it finishes (issue #6).

build.py used to leave HEAD checked out on the issue branch after finishing.
verify.py then runs `git worktree add <workdir> <branch>` on that exact
branch -- which git refuses to do while it's checked out elsewhere,
including the primary working tree. This silently broke every
build -> verify cycle. These tests assert build.main() returns HEAD to
main, and that a subsequent `git worktree add` for the issue branch (the
real thing verify.py does) succeeds afterward.
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

import build  # noqa: E402
import lib  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def _current_branch(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


class BuildLeavesMainCheckedOutTest(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.repo_dir, ignore_errors=True))

        _git(self.repo_dir, "init", "-q")
        _git(self.repo_dir, "config", "user.email", "test@example.com")
        _git(self.repo_dir, "config", "user.name", "Test")
        (self.repo_dir / "file.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "base")
        _git(self.repo_dir, "branch", "-M", "main")

    def _fake_run(self, cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
        if cmd[0] == "gh" and cmd[-1] == ".labels[].name":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == "gh" and cmd[-1] == ".body":
            return SimpleNamespace(returncode=0, stdout="Fix the widget.\n", stderr="")
        # gh issue edit/comment, claude -p, git push (no remote configured
        # in this throwaway repo) -- none of these are asserted on here.
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_leaves_head_on_main_after_finishing(self):
        with mock.patch.object(lib, "run", side_effect=self._fake_run), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(sys, "argv", ["build.py", "1"]):
            rc = build.main()

        self.assertEqual(rc, 0)
        self.assertEqual(_current_branch(self.repo_dir), "main")

    def test_worktree_add_succeeds_for_issue_branch_afterward(self):
        # This is the actual failure mode from issue #6: verify.py runs
        # `git worktree add <workdir> <branch>` right after build.py exits.
        with mock.patch.object(lib, "run", side_effect=self._fake_run), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(sys, "argv", ["build.py", "1"]):
            build.main()

        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(workdir, ignore_errors=True))
        result = subprocess.run(
            ["git", "worktree", "add", str(workdir), "auto/issue-1"],
            cwd=self.repo_dir, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
