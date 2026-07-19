#!/usr/bin/env python3
"""Tests for lib.py: run() and load_config() (issue #73), is_test_file
(issue #7), retry-count/state-label bookkeeping, and daily counter helpers
(issue #9).

lib.run() is the sole subprocess entrypoint every script routes through and
contains Windows-specific branching: it resolves cmd[0] via shutil.which(),
then rewraps the command through cmd.exe /c if the resolved path is a
.cmd/.bat shim (CreateProcess can't launch those directly). These tests
mock shutil.which and subprocess.run to assert the rewrap happens only for
.cmd/.bat, that a plain resolved path passes through unwrapped, and that an
unresolvable cmd[0] leaves the original argv untouched instead of crashing.

lib.load_config() hand-parses the KEY=VALUE config.env format (comment
stripping, blank-line skipping, quote stripping) with no prior test
coverage; these tests exercise comments, blank lines, quoted/unquoted
values, and values containing "=" against a real temp file.

lib.is_test_file() replaces the old unanchored re.compile(r"test|spec",
re.IGNORECASE).search() that both gate.py and verify.py used to detect
test files -- a substring match that also fired on ordinary files like
src/latest.py, contest_winners.py, attestation.py (contain "test") or
docs/specification.md, respected.js (contain "spec"). That let gate.py's
"no test file changed" check be spuriously satisfied by non-test changes,
and made verify.py dispatch non-test files through the pre-fix test runner
(e.g. via npx jest) as if they were real tests.

get_retry_count/set_retry_count and set_state_label drive the issue state
machine (retry:N and state:X labels via `gh issue edit`) and had no test
coverage. These mock lib.run to assert the exact gh label add/remove calls,
the same way test_build.py mocks lib.run for git/gh calls.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402


class RunTest(unittest.TestCase):
    """lib.run() resolves cmd[0] via shutil.which() and, if the resolved
    path is a .cmd/.bat shim, rewraps the whole command through cmd.exe /c
    -- CreateProcess can't launch those directly. Mocks shutil.which and
    subprocess.run so no real process is spawned."""

    def test_resolved_cmd_shim_is_rewrapped_with_cmd_exe(self):
        with mock.patch.object(lib.shutil, "which", return_value=r"C:\npm.cmd"), \
             mock.patch.object(lib.subprocess, "run") as mock_run:
            lib.run(["npm", "install"])

        actual_cmd = mock_run.call_args.args[0]
        self.assertEqual(actual_cmd, ["cmd.exe", "/c", r"C:\npm.cmd", "install"])

    def test_resolved_bat_shim_is_rewrapped_with_cmd_exe(self):
        with mock.patch.object(lib.shutil, "which", return_value=r"C:\tool.bat"), \
             mock.patch.object(lib.subprocess, "run") as mock_run:
            lib.run(["tool", "--flag"])

        actual_cmd = mock_run.call_args.args[0]
        self.assertEqual(actual_cmd, ["cmd.exe", "/c", r"C:\tool.bat", "--flag"])

    def test_resolved_plain_exe_is_passed_through_unwrapped(self):
        with mock.patch.object(lib.shutil, "which", return_value=r"C:\git.exe"), \
             mock.patch.object(lib.subprocess, "run") as mock_run:
            lib.run(["git", "status"])

        actual_cmd = mock_run.call_args.args[0]
        self.assertEqual(actual_cmd, [r"C:\git.exe", "status"])

    def test_unresolvable_cmd_leaves_original_argv_untouched(self):
        with mock.patch.object(lib.shutil, "which", return_value=None), \
             mock.patch.object(lib.subprocess, "run") as mock_run:
            lib.run(["ghost-binary", "arg"])

        actual_cmd = mock_run.call_args.args[0]
        self.assertEqual(actual_cmd, ["ghost-binary", "arg"])

    def test_defaults_are_set_for_text_capture_and_utf8(self):
        with mock.patch.object(lib.shutil, "which", return_value=None), \
             mock.patch.object(lib.subprocess, "run") as mock_run:
            lib.run(["ghost-binary"])

        kwargs = mock_run.call_args.kwargs
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

    def _write_and_load(self, contents: str) -> dict:
        path = self.tmpdir / "config.env"
        path.write_text(contents, encoding="utf-8")
        return lib.load_config(path)

    def test_parses_unquoted_and_quoted_values(self):
        config = self._write_and_load(
            'PLAIN=value\n'
            'DOUBLE_QUOTED="quoted value"\n'
            "SINGLE_QUOTED='also quoted'\n"
        )
        self.assertEqual(config["PLAIN"], "value")
        self.assertEqual(config["DOUBLE_QUOTED"], "quoted value")
        self.assertEqual(config["SINGLE_QUOTED"], "also quoted")

    def test_skips_blank_lines_and_full_line_comments(self):
        config = self._write_and_load(
            "\n"
            "# a full-line comment\n"
            "KEY=value\n"
            "   \n"
        )
        self.assertEqual(config, {"KEY": "value"})

    def test_strips_trailing_comment_after_value(self):
        config = self._write_and_load("KEY=value  # trailing comment\n")
        self.assertEqual(config["KEY"], "value")

    def test_value_containing_equals_sign_is_preserved(self):
        config = self._write_and_load("URL=https://example.com/?a=1&b=2\n")
        self.assertEqual(config["URL"], "https://example.com/?a=1&b=2")

    def test_keys_and_values_are_trimmed_of_surrounding_whitespace(self):
        config = self._write_and_load("  KEY  =  value  \n")
        self.assertEqual(config, {"KEY": "value"})


class IsTestFileFalsePositivesTest(unittest.TestCase):
    """Files that merely contain "test" or "spec" as a substring must not
    be treated as test files."""

    def test_rejects_files_containing_test_substring(self):
        for path in ["src/latest.py", "src/contest_winners.py", "attestation.py"]:
            with self.subTest(path=path):
                self.assertFalse(lib.is_test_file(path))

    def test_rejects_files_containing_spec_substring(self):
        for path in ["docs/specification.md", "respected.js"]:
            with self.subTest(path=path):
                self.assertFalse(lib.is_test_file(path))


class IsTestFileTruePositivesTest(unittest.TestCase):
    """Real test files, named per common conventions, must still match."""

    def test_accepts_conventional_test_file_names(self):
        paths = [
            "scripts/tests/test_gate.py",
            "test_verify.py",
            "src/tests/foo.py",
            "test/foo.py",
            "foo_test.go",
            "foo.test.js",
            "foo.spec.ts",
            "spec/foo_spec.rb",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(lib.is_test_file(path))


class GetRetryCountTest(unittest.TestCase):
    def _fake_run(self, labels: str):
        def fake(cmd, **kwargs):
            return SimpleNamespace(returncode=0, stdout=labels, stderr="")
        return fake

    def test_no_retry_label_returns_zero(self):
        with mock.patch.object(lib, "run", side_effect=self._fake_run("state:ready\nbug\n")):
            self.assertEqual(lib.get_retry_count("1"), 0)

    def test_parses_retry_label(self):
        with mock.patch.object(lib, "run", side_effect=self._fake_run("state:ready\nretry:2\nbug\n")):
            self.assertEqual(lib.get_retry_count("1"), 2)


class SetRetryCountTest(unittest.TestCase):
    def test_removes_old_label_and_adds_new(self):
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:4] == ["gh", "issue", "view", "1"]:
                return SimpleNamespace(returncode=0, stdout="retry:1\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(lib, "run", side_effect=fake):
            lib.set_retry_count("1", 2)

        self.assertIn(["gh", "issue", "edit", "1", "--remove-label", "retry:1"], calls)
        self.assertIn(["gh", "issue", "edit", "1", "--add-label", "retry:2"], calls)


class SetStateLabelTest(unittest.TestCase):
    def test_removes_all_known_states_and_adds_new_one(self):
        calls = []

        def fake(cmd, **kwargs):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(lib, "run", side_effect=fake):
            lib.set_state_label("1", "in-review")

        for state in ("triage", "ready", "in-progress", "in-review", "needs-human"):
            self.assertIn(["gh", "issue", "edit", "1", "--remove-label", f"state:{state}"], calls)
        self.assertIn(["gh", "issue", "edit", "1", "--add-label", "state:in-review"], calls)
        # The add must come after all the removes, else a stale label could
        # win a race against `gh`'s own label state.
        add_index = calls.index(["gh", "issue", "edit", "1", "--add-label", "state:in-review"])
        remove_indices = [
            i for i, c in enumerate(calls) if len(c) > 4 and c[4] == "--remove-label"
        ]
        self.assertTrue(all(i < add_index for i in remove_indices))


class CfgHelpersTest(unittest.TestCase):
    def test_cfg_int_parses_digits(self):
        self.assertEqual(lib.cfg_int({"MAX_DIFF_LINES": "400"}, "MAX_DIFF_LINES"), 400)

    def test_cfg_bool_true_variants(self):
        for value in ("true", "True", " TRUE "):
            self.assertTrue(lib.cfg_bool({"K": value}, "K"), value)

    def test_cfg_bool_false_variants(self):
        for value in ("false", "0", "", "yes"):
            self.assertFalse(lib.cfg_bool({"K": value}, "K"), value)


class CounterStateTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))
        self.state_file = self.tmpdir / "state.json"
        self.state_file.write_text('{"daily_cycles": 0, "daily_claude_calls": 0, "date": ""}', encoding="utf-8")
        self._patcher = mock.patch.object(lib, "STATE_FILE", self.state_file)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_bump_counter_increments_and_get_counter_reads_it_back(self):
        lib.bump_counter("daily_cycles")
        lib.bump_counter("daily_cycles")
        self.assertEqual(lib.get_counter("daily_cycles"), 2)

    def test_reset_daily_counters_resets_on_new_day(self):
        import json
        self.state_file.write_text(
            json.dumps({"daily_cycles": 5, "daily_claude_calls": 9, "date": "2000-01-01"}),
            encoding="utf-8",
        )

        lib.reset_daily_counters_if_new_day()

        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["daily_cycles"], 0)
        self.assertEqual(state["daily_claude_calls"], 0)
        self.assertNotEqual(state["date"], "2000-01-01")

    def test_reset_daily_counters_is_noop_on_same_day(self):
        import json
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.state_file.write_text(
            json.dumps({"daily_cycles": 5, "daily_claude_calls": 9, "date": today}),
            encoding="utf-8",
        )

        lib.reset_daily_counters_if_new_day()

        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["daily_cycles"], 5)
        self.assertEqual(state["daily_claude_calls"], 9)


if __name__ == "__main__":
    unittest.main()
