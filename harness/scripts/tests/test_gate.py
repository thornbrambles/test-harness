#!/usr/bin/env python3
"""Tests for gate.check_gate's individual conditions (issue #9).

gate.py implements the harness's deterministic safety checks -- forbidden
paths, diff size, oscillation detection -- with no prior test coverage.
These tests exercise each condition against a real temp git repo (the
pattern already used in test_verify.py / test_build.py), so a future change
that silently breaks one of these checks gets caught.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import gate  # noqa: E402
import lib  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


class GateCheckTestBase(unittest.TestCase):
    CONFIG = {"FORBIDDEN_PATH_REGEX": "migrations/|secrets/", "MAX_DIFF_LINES": "400"}

    def setUp(self):
        self.repo_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.repo_dir, ignore_errors=True))

        _git(self.repo_dir, "init", "-q")
        _git(self.repo_dir, "config", "user.email", "test@example.com")
        _git(self.repo_dir, "config", "user.name", "Test")
        (self.repo_dir / "test_thing.py").write_text("import unittest\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "base")
        _git(self.repo_dir, "branch", "-M", "main")
        _git(self.repo_dir, "branch", "base")

        _git(self.repo_dir, "checkout", "-q", "-b", "auto/issue-1")

    def _fake_run(self, cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
        return mock.DEFAULT

    def _check_gate(self, config=None):
        old_cwd = Path.cwd()
        import os
        os.chdir(self.repo_dir)
        try:
            with mock.patch.object(lib, "run", side_effect=self._fake_run):
                return gate.check_gate("1", "auto/issue-1", "base", config or self.CONFIG)
        finally:
            os.chdir(old_cwd)


class ForbiddenPathTest(GateCheckTestBase):
    def test_rejects_forbidden_path(self):
        (self.repo_dir / "migrations").mkdir()
        (self.repo_dir / "migrations" / "0001.sql").write_text("ALTER TABLE\n", encoding="utf-8")
        (self.repo_dir / "test_thing.py").write_text("import unittest\nX = 1\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "touch forbidden path")

        passed, reason = self._check_gate()

        self.assertFalse(passed)
        self.assertIn("forbidden path", reason)
        self.assertIn("migrations/0001.sql", reason)

    def test_allows_non_forbidden_path(self):
        (self.repo_dir / "test_thing.py").write_text("import unittest\nX = 1\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "touch test file")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)


class DiffSizeTest(GateCheckTestBase):
    def test_rejects_oversized_diff(self):
        lines = "\n".join(f"line{i}" for i in range(20))
        (self.repo_dir / "test_thing.py").write_text(lines + "\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "big change")

        passed, reason = self._check_gate(config={**self.CONFIG, "MAX_DIFF_LINES": "5"})

        self.assertFalse(passed)
        self.assertIn("diff too large", reason)

    def test_allows_diff_under_limit(self):
        (self.repo_dir / "test_thing.py").write_text("import unittest\nX = 1\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "small change")

        passed, reason = self._check_gate(config={**self.CONFIG, "MAX_DIFF_LINES": "5"})

        self.assertTrue(passed, reason)


class NoTestFileChangedTest(GateCheckTestBase):
    def test_rejects_when_no_test_file_touched(self):
        (self.repo_dir / "src.py").write_text("X = 1\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "only source changed")

        passed, reason = self._check_gate()

        self.assertFalse(passed)
        self.assertEqual(reason, "no test file changed")

    def test_allows_when_test_file_touched(self):
        (self.repo_dir / "src.py").write_text("X = 1\n", encoding="utf-8")
        (self.repo_dir / "test_thing.py").write_text("import unittest\nX = 1\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "source + test changed")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)


class OscillationTest(GateCheckTestBase):
    def test_detects_oscillation_across_three_small_commits(self):
        for marker in ("x", "y", "z"):
            with (self.repo_dir / "test_thing.py").open("a", encoding="utf-8") as f:
                f.write(f"{marker}\n")
            _git(self.repo_dir, "add", ".")
            _git(self.repo_dir, "commit", "-q", "-m", f"thrash {marker}")

        passed, reason = self._check_gate()

        self.assertFalse(passed)
        self.assertIn("oscillation detected", reason)

    def test_no_oscillation_when_progress_is_real(self):
        for i, marker in enumerate(("aaaaaa", "bbbbbb", "cccccc")):
            with (self.repo_dir / "test_thing.py").open("a", encoding="utf-8") as f:
                f.write("\n".join(f"{marker}{n}" for n in range(10)) + "\n")
            _git(self.repo_dir, "add", ".")
            _git(self.repo_dir, "commit", "-q", "-m", f"real progress {i}")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)

    def test_no_oscillation_check_under_three_commits(self):
        with (self.repo_dir / "test_thing.py").open("a", encoding="utf-8") as f:
            f.write("x\n")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "single small commit")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)


class LintTest(GateCheckTestBase):
    def setUp(self):
        super().setUp()
        self.npm_calls = []
        self.npm_result = subprocess.CompletedProcess(["npm", "run", "lint"], 0, "", "")

    def _fake_run(self, cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
        if cmd == ["npm", "run", "lint"]:
            self.npm_calls.append(cmd)
            return self.npm_result
        return mock.DEFAULT

    def _commit_package_json(self, contents: str):
        (self.repo_dir / "package.json").write_text(contents, encoding="utf-8")
        with (self.repo_dir / "test_thing.py").open("a", encoding="utf-8") as f:
            f.write("lint-test\n")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "add package.json")

    def test_no_package_json_skips_lint_check(self):
        with (self.repo_dir / "test_thing.py").open("a", encoding="utf-8") as f:
            f.write("no-pkg\n")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "no package.json")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)
        self.assertEqual(self.npm_calls, [])

    def test_package_json_without_lint_script_passes(self):
        self._commit_package_json('{"scripts": {"build": "tsc"}}')

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)
        self.assertEqual(self.npm_calls, [])

    def test_lint_failure_rejects_with_last_five_lines(self):
        self._commit_package_json('{"scripts": {"lint": "eslint ."}}')
        lines = [f"error line {i}" for i in range(8)]
        self.npm_result = subprocess.CompletedProcess(["npm", "run", "lint"], 1, "\n".join(lines), "")

        passed, reason = self._check_gate()

        self.assertFalse(passed)
        self.assertEqual(reason, "lint failed: " + "\n".join(lines[-5:]))
        self.assertEqual(len(self.npm_calls), 1)

    def test_lint_success_does_not_affect_gate(self):
        self._commit_package_json('{"scripts": {"lint": "eslint ."}}')
        self.npm_result = subprocess.CompletedProcess(["npm", "run", "lint"], 0, "", "")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)
        self.assertEqual(len(self.npm_calls), 1)

    def test_malformed_package_json_treated_as_no_scripts(self):
        self._commit_package_json("{not valid json")

        passed, reason = self._check_gate()

        self.assertTrue(passed, reason)
        self.assertEqual(self.npm_calls, [])


if __name__ == "__main__":
    unittest.main()
