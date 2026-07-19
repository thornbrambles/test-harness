#!/usr/bin/env python3
"""Tests for verify.py: pre_fix_test_output (issue #2) and the main()
decision logic that acts on gate/verdict results (issue #62).

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


class PreFixTestOutputNewSourceFileTest(unittest.TestCase):
    """Issue #10: if the fix's logic lives in a brand-new source file (not
    just a modified one), `git checkout base -- .` never deletes it, since
    that command only updates paths that exist in `base`. The pre-fix run
    must not see the new file's post-fix content."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

        _git(self.tmpdir, "init", "-q")
        _git(self.tmpdir, "config", "user.email", "test@example.com")
        _git(self.tmpdir, "config", "user.name", "Test")

        (self.tmpdir / "README.txt").write_text("base\n", encoding="utf-8")
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "base")
        _git(self.tmpdir, "branch", "base")

        _git(self.tmpdir, "checkout", "-q", "-b", "fix")
        # The fix adds a brand-new module the new test imports/uses.
        (self.tmpdir / "newmodule.py").write_text(
            "def value():\n    return 'NEW_MODULE_VALUE'\n", encoding="utf-8"
        )
        (self.tmpdir / "mytest.py").write_text(
            "import newmodule\nprint(newmodule.value())\n", encoding="utf-8"
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

    def test_new_source_file_is_removed_before_pre_fix_run(self):
        output = self._run_in_repo(
            lambda: verify.pre_fix_test_output("base", "fix", ["mytest.py"])
        )
        # Against genuinely old behavior, newmodule.py must not exist, so
        # the new test should fail (ModuleNotFoundError), not print the
        # post-fix value.
        self.assertNotIn("NEW_MODULE_VALUE", output)

    def test_new_source_file_is_restored_afterwards(self):
        self._run_in_repo(
            lambda: verify.pre_fix_test_output("base", "fix", ["mytest.py"])
        )
        self.assertEqual(
            (self.tmpdir / "newmodule.py").read_text(encoding="utf-8"),
            "def value():\n    return 'NEW_MODULE_VALUE'\n",
        )


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


class ChangedTestFilesFalsePositiveTest(unittest.TestCase):
    """changed_test_files must not treat a file that merely contains "test"
    or "spec" as a substring (e.g. latest_prices.py) as a test file (issue
    #7) -- otherwise pre_fix_test_output would dispatch it through the
    pre-fix test runner as if it were real test evidence."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

        _git(self.tmpdir, "init", "-q")
        _git(self.tmpdir, "config", "user.email", "test@example.com")
        _git(self.tmpdir, "config", "user.name", "Test")

        (self.tmpdir / "latest_prices.py").write_text("OLD\n", encoding="utf-8")
        (self.tmpdir / "test_real.py").write_text("OLD\n", encoding="utf-8")
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "base")
        _git(self.tmpdir, "branch", "base")

        _git(self.tmpdir, "checkout", "-q", "-b", "fix")
        (self.tmpdir / "latest_prices.py").write_text("NEW\n", encoding="utf-8")
        (self.tmpdir / "test_real.py").write_text("NEW\n", encoding="utf-8")
        _git(self.tmpdir, "add", ".")
        _git(self.tmpdir, "commit", "-q", "-m", "fix")

    def test_excludes_substring_match_but_includes_real_test(self):
        old_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        try:
            result = verify.changed_test_files("base", "fix")
        finally:
            os.chdir(old_cwd)
        self.assertNotIn("latest_prices.py", result)
        self.assertIn("test_real.py", result)


class WorktreeAddFailureTest(unittest.TestCase):
    """Reproduces the exact scenario from issue #6: the target branch is
    already checked out in the primary working tree (as build.py used to
    leave it), so `git worktree add` fails. verify.main() must detect that
    failure and bail out instead of silently chdir'ing into an empty temp
    directory and letting gate.check_gate run against a non-repo.

    lib.load_config is mocked because the real config.env is gitignored and
    so wouldn't exist in a fresh checkout -- exactly the environment this
    test (and verify.py's own test run) executes in."""

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
             mock.patch.object(lib, "load_config", return_value={}), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(gate, "check_gate") as mock_check_gate, \
             mock.patch.object(sys, "argv", ["verify.py", "1", "auto/issue-1"]):
            rc = verify.main()

        self.assertEqual(rc, 1)
        mock_check_gate.assert_not_called()


class _MainPipelineTestBase(unittest.TestCase):
    """Shared fixture for driving verify.main() through the post-gate
    decision logic (verify.py:150-185) with gate.check_gate mocked out (its
    own behavior is covered separately) and every `gh`/`claude` call
    intercepted, while `git worktree add/remove` and `git diff` run for
    real against a throwaway repo -- so main() exercises its actual control
    flow instead of a hand-rolled stand-in for it."""

    issue = "62"
    branch = "auto/issue-62"

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

        _git(self.repo_dir, "checkout", "-q", "-b", self.branch)
        (self.repo_dir / "file.txt").write_text("fixed\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".")
        _git(self.repo_dir, "commit", "-q", "-m", "fix")
        # Leave "main" checked out in the primary tree so `git worktree add`
        # for self.branch below succeeds (mirrors real daemon usage).
        _git(self.repo_dir, "checkout", "-q", "main")

        self.gh_calls = []
        self.old_cwd = os.getcwd()
        os.chdir(self.repo_dir)
        self.addCleanup(lambda: os.chdir(self.old_cwd))

    # Overridden by subclasses that want to simulate a failing `gh` call.
    pr_create_returncode = 0
    pr_merge_returncode = 0

    def _fake_run(self, cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
        if cmd[0] == "claude":
            return SimpleNamespace(returncode=0, stdout=self.verdict_text, stderr="")
        if cmd[0] == "gh":
            self.gh_calls.append(cmd)
            if cmd[:4] == ["gh", "pr", "list", "--head"]:
                return SimpleNamespace(returncode=0, stdout=self.existing_pr_num, stderr="")
            if cmd[:3] == ["gh", "issue", "view"]:
                return SimpleNamespace(returncode=0, stdout=self.retry_label, stderr="")
            if cmd[:3] == ["gh", "pr", "create"]:
                return SimpleNamespace(
                    returncode=self.pr_create_returncode,
                    stdout="",
                    stderr="" if self.pr_create_returncode == 0 else "create failed",
                )
            if cmd[:3] == ["gh", "pr", "merge"]:
                return SimpleNamespace(
                    returncode=self.pr_merge_returncode,
                    stdout="",
                    stderr="" if self.pr_merge_returncode == 0 else "merge failed",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def _run_main(self):
        with mock.patch.object(lib, "run", side_effect=self._fake_run), \
             mock.patch.object(lib, "load_config", return_value={"MAX_RETRIES": "3"}), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(gate, "check_gate", return_value=(True, "")), \
             mock.patch.object(sys, "argv", ["verify.py", self.issue, self.branch]):
            return verify.main()

    def _gh_call(self, *prefix):
        for cmd in self.gh_calls:
            if cmd[: len(prefix)] == list(prefix):
                return cmd
        return None


class MainApproveTest(_MainPipelineTestBase):
    """VERDICT: APPROVE with no existing open PR must create the PR, squash-
    merge it, and close the issue (verify.py:157-170) -- the exact sequence
    issue #13 flags as unverified today."""

    verdict_text = "VERDICT: APPROVE\nLooks good.\n"
    existing_pr_num = ""
    retry_label = ""

    def test_approve_creates_merges_and_closes(self):
        rc = self._run_main()

        self.assertEqual(rc, 0)
        self.assertIsNotNone(self._gh_call("gh", "pr", "create"))
        merge_call = self._gh_call("gh", "pr", "merge", self.branch, "--squash", "--delete-branch")
        self.assertIsNotNone(merge_call)
        close_call = self._gh_call("gh", "issue", "close", self.issue)
        self.assertIsNotNone(close_call)


class MainApproveExistingPrTest(_MainPipelineTestBase):
    """When a PR already exists for the branch, APPROVE must comment on it
    instead of creating a duplicate, but still squash-merge and close."""

    verdict_text = "VERDICT: APPROVE\n"
    existing_pr_num = "5"
    retry_label = ""

    def test_approve_with_existing_pr_comments_instead_of_creating(self):
        rc = self._run_main()

        self.assertEqual(rc, 0)
        self.assertIsNone(self._gh_call("gh", "pr", "create"))
        self.assertIsNotNone(self._gh_call("gh", "pr", "comment", "5"))
        self.assertIsNotNone(
            self._gh_call("gh", "pr", "merge", self.branch, "--squash", "--delete-branch")
        )


class MainApproveMergeFailureTest(_MainPipelineTestBase):
    """Issue #13: if `gh pr merge` fails, main() must not report APPROVED or
    close the issue -- it must surface the failure and return non-zero."""

    verdict_text = "VERDICT: APPROVE\nLooks good.\n"
    existing_pr_num = ""
    retry_label = ""
    pr_merge_returncode = 1

    def test_merge_failure_does_not_close_issue_or_report_success(self):
        rc = self._run_main()

        self.assertEqual(rc, 1)
        self.assertIsNotNone(self._gh_call("gh", "pr", "merge", self.branch, "--squash", "--delete-branch"))
        self.assertIsNone(self._gh_call("gh", "issue", "close", self.issue))


class MainApproveCreateFailureTest(_MainPipelineTestBase):
    """Issue #13: if `gh pr create` fails, main() must not proceed to merge
    or close the issue -- it must surface the failure and return non-zero."""

    verdict_text = "VERDICT: APPROVE\nLooks good.\n"
    existing_pr_num = ""
    retry_label = ""
    pr_create_returncode = 1

    def test_create_failure_does_not_merge_close_or_report_success(self):
        rc = self._run_main()

        self.assertEqual(rc, 1)
        self.assertIsNotNone(self._gh_call("gh", "pr", "create"))
        self.assertIsNone(self._gh_call("gh", "pr", "merge", self.branch, "--squash", "--delete-branch"))
        self.assertIsNone(self._gh_call("gh", "issue", "close", self.issue))


class MainRejectRetryTest(_MainPipelineTestBase):
    """VERDICT: REJECT below MAX_RETRIES must bump the retry:N label and
    route back to state:ready, not state:needs-human (verify.py:172-185)."""

    verdict_text = "VERDICT: REJECT\nREASON: the new test does not fail on old code\n"
    existing_pr_num = ""
    retry_label = "retry:0"

    def test_reject_under_max_retries_bumps_retry_and_stays_ready(self):
        rc = self._run_main()

        self.assertEqual(rc, 1)
        self.assertIsNotNone(self._gh_call("gh", "issue", "edit", self.issue, "--add-label", "retry:1"))
        self.assertIsNotNone(
            self._gh_call("gh", "issue", "edit", self.issue, "--add-label", "state:ready")
        )
        self.assertIsNone(
            self._gh_call("gh", "issue", "edit", self.issue, "--add-label", "state:needs-human")
        )
        comment_call = self._gh_call("gh", "issue", "comment", self.issue)
        self.assertIn("REASON: the new test does not fail on old code", comment_call[-1])


class MainRejectMaxRetriesTest(_MainPipelineTestBase):
    """VERDICT: REJECT once new_retry reaches MAX_RETRIES (3) must route to
    state:needs-human instead of bumping the retry label further."""

    verdict_text = "VERDICT: REJECT\nREASON: still broken\n"
    existing_pr_num = ""
    retry_label = "retry:2"

    def test_reject_at_max_retries_routes_to_needs_human(self):
        rc = self._run_main()

        self.assertEqual(rc, 1)
        self.assertIsNotNone(
            self._gh_call("gh", "issue", "edit", self.issue, "--add-label", "state:needs-human")
        )
        self.assertIsNone(
            self._gh_call("gh", "issue", "edit", self.issue, "--add-label", "state:ready")
        )
        self.assertIsNone(self._gh_call("gh", "issue", "edit", self.issue, "--add-label", "retry:3"))


if __name__ == "__main__":
    unittest.main()
