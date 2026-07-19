#!/usr/bin/env python3
"""Tests for verify.pre_fix_test_output (issue #2).

A repo-wide `git checkout <base> -- .` reverts every tracked file back to
the pre-fix commit, including the changed test file(s) themselves whenever
they already existed at base. That makes the "pre-fix" run just re-run the
*old* test against the *old* source -- which trivially passes -- instead of
the *new* test against the *old* source, defeating the discriminating-test
check the Verifier relies on. These tests assert the fixed behavior: source
is reverted to pre-fix, but the changed test file keeps its post-fix
content while the pre-fix run executes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import gate  # noqa: E402
import lib  # noqa: E402
import verify  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


class PreFixTestOutputTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

        _git(self.tmpdir, "init", "-q")
        _git(self.tmpdir, "config", "user.email", "test@example.com")
        _git(self.tmpdir, "config", "user.name", "Test")

        (self.tmpdir / "src.txt").write_text("OLD_SOURCE\n", encoding="utf-8")
        (self.tmpdir / "mytest.sh").write_text(
            "#!/usr/bin/env bash\necho OLD_TEST_MARKER\n", encoding="utf-8"
        )
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "base")
        _git(self.tmpdir, "branch", "base")

        _git(self.tmpdir, "checkout", "-q", "-b", "fix")
        (self.tmpdir / "src.txt").write_text("NEW_SOURCE\n", encoding="utf-8")
        (self.tmpdir / "mytest.sh").write_text(
            "#!/usr/bin/env bash\ncat src.txt\n", encoding="utf-8"
        )
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "fix")

    def _run_in_repo(self, fn):
        old_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        try:
            return fn()
        finally:
            os.chdir(old_cwd)

    def test_reverts_source_but_keeps_new_test_content(self):
        # The new/changed test (cats src.txt) must run against the reverted
        # (pre-fix) source, not against a reverted copy of itself.
        output = self._run_in_repo(
            lambda: verify.pre_fix_test_output("base", "fix", ["mytest.sh"])
        )
        self.assertIn("OLD_SOURCE", output)
        self.assertNotIn("OLD_TEST_MARKER", output)

    def test_restores_branch_state_afterwards(self):
        self._run_in_repo(
            lambda: verify.pre_fix_test_output("base", "fix", ["mytest.sh"])
        )
        self.assertEqual(
            (self.tmpdir / "src.txt").read_text(encoding="utf-8").strip(), "NEW_SOURCE"
        )
        self.assertEqual(
            (self.tmpdir / "mytest.sh").read_text(encoding="utf-8"),
            "#!/usr/bin/env bash\ncat src.txt\n",
        )

    def test_no_test_files_skips_pre_fix_run(self):
        output = self._run_in_repo(lambda: verify.pre_fix_test_output("base", "fix", []))
        self.assertIn("skipped", output)


class DetectTestCmdPythonFallbackTest(unittest.TestCase):
    """Without a package.json test script or run_tests.sh, a nested
    test_*.py layout must still be picked up (issue #2 retry feedback: this
    repo has neither, so the post-fix full-suite run reported "no test
    runner detected" and never touched the new tests)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))
        tests_dir = self.tmpdir / "scripts" / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_thing.py").write_text("import unittest\n", encoding="utf-8")

    def test_finds_nested_python_unittest_layout(self):
        old_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        try:
            cmd = verify.detect_test_cmd()
        finally:
            os.chdir(old_cwd)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[:4], ["python", "-m", "unittest", "discover"])
        self.assertEqual(Path(cmd[5]).name, "tests")


class PreFixTestOutputPythonDispatchTest(unittest.TestCase):
    """A changed .py test file must run via the Python interpreter, not fall
    through to the npx/jest branch meant for JS tests (issue #2 retry
    feedback: that fallback broke pre-fix evidence for non-JS test files)."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

        _git(self.tmpdir, "init", "-q")
        _git(self.tmpdir, "config", "user.email", "test@example.com")
        _git(self.tmpdir, "config", "user.name", "Test")

        (self.tmpdir / "src.txt").write_text("OLD_SOURCE\n", encoding="utf-8")
        (self.tmpdir / "mytest.py").write_text(
            "print('OLD_TEST_MARKER')\n", encoding="utf-8"
        )
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "base")
        _git(self.tmpdir, "branch", "base")

        _git(self.tmpdir, "checkout", "-q", "-b", "fix")
        (self.tmpdir / "src.txt").write_text("NEW_SOURCE\n", encoding="utf-8")
        (self.tmpdir / "mytest.py").write_text(
            "print(open('src.txt', encoding='utf-8').read())\n", encoding="utf-8"
        )
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "fix")

    def _run_in_repo(self, fn):
        old_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        try:
            return fn()
        finally:
            os.chdir(old_cwd)

    def test_dispatches_py_test_files_via_python_not_jest(self):
        output = self._run_in_repo(
            lambda: verify.pre_fix_test_output("base", "fix", ["mytest.py"])
        )
        self.assertIn("OLD_SOURCE", output)
        self.assertNotIn("OLD_TEST_MARKER", output)
        self.assertNotIn("Could not find a config file", output)


class WorktreeAddFailureTest(unittest.TestCase):
    """Reproduces the exact scenario from issue #6: the target branch is
    already checked out in the primary working tree (as build.py used to
    leave it), so `git worktree add` fails. verify.main() must detect that
    failure and bail out instead of silently chdir'ing into an empty temp
    directory and letting gate.check_gate run against a non-repo."""

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

        # Mirror build.py's old (buggy) behavior: leave the issue branch
        # checked out right here in the primary working tree.
        _git(self.repo_dir, "checkout", "-q", "-b", "auto/issue-1")

    def _fake_run(self, cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_aborts_without_running_gate_when_worktree_add_fails(self):
        with mock.patch.object(lib, "run", side_effect=self._fake_run), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(gate, "check_gate") as mock_check_gate, \
             mock.patch.object(sys, "argv", ["verify.py", "1", "auto/issue-1"]):
            rc = verify.main()

        self.assertEqual(rc, 1)
        mock_check_gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
