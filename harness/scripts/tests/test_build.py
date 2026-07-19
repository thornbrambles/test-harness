#!/usr/bin/env python3
"""Tests for build.py leaving HEAD on main when it finishes (issue #6).

build.py used to leave HEAD checked out on the issue branch after finishing.
verify.py then runs `git worktree add <workdir> <branch>` on that exact
branch -- which git refuses to do while it's checked out elsewhere,
including the primary working tree. This silently broke every
build -> verify cycle. These tests assert build.main() returns HEAD to
main, and that a subsequent `git worktree add` for the issue branch (the
real thing verify.py does) succeeds afterward.

lib.load_config is mocked rather than left to read the real config.env: that
file is gitignored, so it's absent from any fresh clone or `git worktree
add` checkout (exactly the environment verify.py's own test run happens in)
-- reading it for real would make these tests fail with FileNotFoundError
regardless of the fix under test.
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
        if cmd[0] == "claude":
            # Simulate a real Builder run: it edits a file and commits on
            # the checked-out branch before build.py inspects HEAD again.
            (self.repo_dir / "file.txt").write_text("fixed\n", encoding="utf-8")
            _git(self.repo_dir, "add", ".")
            _git(self.repo_dir, "commit", "-q", "-m", "builder commit")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        # gh issue edit/comment, git push (no remote configured in this
        # throwaway repo) -- none of these are asserted on here.
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def test_leaves_head_on_main_after_finishing(self):
        with mock.patch.object(lib, "run", side_effect=self._fake_run), \
             mock.patch.object(lib, "load_config", return_value={"FORBIDDEN_PATH_REGEX": "migrations/", "MAX_DIFF_LINES": "400"}), \
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
             mock.patch.object(lib, "load_config", return_value={"FORBIDDEN_PATH_REGEX": "migrations/", "MAX_DIFF_LINES": "400"}), \
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


class BuildInfraFailureTest(unittest.TestCase):
    """Tests for issue #8: build.py used to ignore the claude subprocess's
    outcome entirely and unconditionally push, label the issue in-review,
    and log build_complete -- even when the Builder crashed or made no
    commits at all. That silently burned a retry on what verify.py's gate
    would then reject for the unrelated "no test file changed" reason,
    masking a genuine infra failure as a rejected fix. These tests assert
    build.main() instead detects the failure and routes straight to
    needs-human without ever claiming in-review/build_complete."""

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

    def _fake_run(self, claude_returncode, make_commit):
        def _run(cmd, **kwargs):
            if cmd[0] == "git":
                return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
            if cmd[0] == "gh" and cmd[-1] == ".labels[].name":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh" and cmd[-1] == ".body":
                return SimpleNamespace(returncode=0, stdout="Fix the widget.\n", stderr="")
            if cmd[0] == "claude":
                if make_commit:
                    (self.repo_dir / "file.txt").write_text("fixed\n", encoding="utf-8")
                    _git(self.repo_dir, "add", ".")
                    _git(self.repo_dir, "commit", "-q", "-m", "builder commit")
                return SimpleNamespace(returncode=claude_returncode, stdout="", stderr="rate limited")
            # gh issue edit/comment, git push (no remote configured in this
            # throwaway repo) -- none of these are asserted on here.
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _run

    def _run_build(self, claude_returncode, make_commit):
        with mock.patch.object(lib, "run", side_effect=self._fake_run(claude_returncode, make_commit)), \
             mock.patch.object(lib, "load_config", return_value={"FORBIDDEN_PATH_REGEX": "migrations/", "MAX_DIFF_LINES": "400"}), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event") as mock_log, \
             mock.patch.object(lib, "set_state_label") as mock_label, \
             mock.patch.object(sys, "argv", ["build.py", "1"]):
            rc = build.main()
        return rc, mock_log, mock_label

    def test_nonzero_exit_routes_to_needs_human_without_burning_a_retry(self):
        rc, mock_log, mock_label = self._run_build(claude_returncode=1, make_commit=False)

        self.assertEqual(rc, 1)
        mock_label.assert_any_call("1", "needs-human")
        self.assertNotIn(mock.call("1", "in-review"), mock_label.call_args_list)
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("build_infra_failure", logged_events)
        self.assertNotIn("build_complete", logged_events)

    def test_zero_exit_but_no_commits_routes_to_needs_human(self):
        # claude can exit 0 having decided to make no changes at all --
        # that's still not a real build to hand to the Verifier.
        rc, mock_log, mock_label = self._run_build(claude_returncode=0, make_commit=False)

        self.assertEqual(rc, 1)
        mock_label.assert_any_call("1", "needs-human")
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("build_infra_failure", logged_events)
        self.assertNotIn("build_complete", logged_events)

    def test_successful_build_still_reaches_in_review(self):
        rc, mock_log, mock_label = self._run_build(claude_returncode=0, make_commit=True)

        self.assertEqual(rc, 0)
        mock_label.assert_any_call("1", "in-review")
        logged_events = [c.args[0] for c in mock_log.call_args_list]
        self.assertIn("build_complete", logged_events)
        self.assertNotIn("build_infra_failure", logged_events)


class BuildPromptUsesConfiguredMaxDiffLinesTest(unittest.TestCase):
    """Tests for issue #64: builder.md used to hardcode the literal "400" as
    the diff-size limit shown to the Builder, instead of templating
    {{MAX_DIFF_LINES}} from config.env like FORBIDDEN_PATH_REGEX already is.
    If an operator (or the Tuner) changed MAX_DIFF_LINES away from 400, the
    Builder's self-check would silently keep comparing against the stale
    hardcoded value. These tests assert the real builder.md template is
    rendered with the configured MAX_DIFF_LINES value baked into the prompt
    handed to `claude`, using a config value other than 400 so the test
    would fail on the old hardcoded-"400" prompt text."""

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

    def test_prompt_reflects_configured_max_diff_lines(self):
        captured_prompt = {}

        def _fake_run(cmd, **kwargs):
            if cmd[0] == "git":
                return subprocess.run(cmd, cwd=self.repo_dir, text=True, capture_output=True)
            if cmd[0] == "gh" and cmd[-1] == ".labels[].name":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh" and cmd[-1] == ".body":
                return SimpleNamespace(returncode=0, stdout="Fix the widget.\n", stderr="")
            if cmd[0] == "claude":
                captured_prompt["text"] = cmd[cmd.index("-p") + 1]
                (self.repo_dir / "file.txt").write_text("fixed\n", encoding="utf-8")
                _git(self.repo_dir, "add", ".")
                _git(self.repo_dir, "commit", "-q", "-m", "builder commit")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(lib, "run", side_effect=_fake_run), \
             mock.patch.object(
                 lib, "load_config",
                 return_value={"FORBIDDEN_PATH_REGEX": "migrations/", "MAX_DIFF_LINES": "250"},
             ), \
             mock.patch.object(lib, "bump_counter"), \
             mock.patch.object(lib, "log_event"), \
             mock.patch.object(sys, "argv", ["build.py", "1"]):
            rc = build.main()

        self.assertEqual(rc, 0)
        prompt = captured_prompt["text"]
        self.assertIn("under 250", prompt)
        self.assertNotIn("{{MAX_DIFF_LINES}}", prompt)
        self.assertNotIn("under 400", prompt)


class RenderSinglePassSubstitutionTest(unittest.TestCase):
    """Tests for issue #39: render() used to substitute {{KEY}} placeholders
    via N sequential str.replace calls over a mutating string. If an
    earlier-substituted value (e.g. attacker/user-controlled ISSUE_BODY)
    happened to contain the literal text of a later key's placeholder token,
    that later iteration would expand it -- reinjecting content the issue
    author never provided into their own rendered section."""

    def test_placeholder_inside_earlier_value_is_not_expanded(self):
        template = "ISSUE:{{ISSUE_BODY}}\nFEEDBACK:{{PRIOR_FEEDBACK}}"
        values = {
            "ISSUE_BODY": "Please fix this. {{PRIOR_FEEDBACK}}",
            "PRIOR_FEEDBACK": "REASON: secret internal retry notes from a previous attempt",
        }

        out = build.render(template, values)

        self.assertEqual(
            out,
            "ISSUE:Please fix this. {{PRIOR_FEEDBACK}}\n"
            "FEEDBACK:REASON: secret internal retry notes from a previous attempt",
        )
        self.assertNotIn("secret internal retry notes", out.split("FEEDBACK:")[0])

    def test_plain_substitution_still_works(self):
        template = "{{A}}-{{B}}-{{C}}"
        out = build.render(template, {"A": "1", "B": "2", "C": "3"})
        self.assertEqual(out, "1-2-3")

    def test_conditional_block_still_expands_before_substitution(self):
        template = "{{#FLAG}}shown: {{FLAG}}{{/FLAG}}"
        out = build.render(template, {"FLAG": "yes"})
        self.assertEqual(out, "shown: yes")

    def test_conditional_block_dropped_when_falsy(self):
        template = "before{{#FLAG}}shown: {{FLAG}}{{/FLAG}}after"
        out = build.render(template, {"FLAG": ""})
        self.assertEqual(out, "beforeafter")

    def test_unknown_placeholder_left_literal(self):
        out = build.render("{{UNKNOWN}}", {"OTHER": "x"})
        self.assertEqual(out, "{{UNKNOWN}}")


if __name__ == "__main__":
    unittest.main()
