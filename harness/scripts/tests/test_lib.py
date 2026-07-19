#!/usr/bin/env python3
"""Tests for lib.py: is_test_file (issue #7), retry-count/state-label
bookkeeping, and daily counter helpers (issue #9).

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

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import lib  # noqa: E402


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

    def test_read_state_falls_back_to_default_on_corrupt_json(self):
        # Simulates a process killed mid-write leaving a truncated file
        # (issue #50): the old code did json.loads() with no try/except,
        # so any caller (bump_counter, get_counter, get_last_run, ...)
        # would raise JSONDecodeError and crash the daemon loop forever.
        self.state_file.write_text('{"daily_cycles": 3, "date": "2020', encoding="utf-8")

        state = lib._read_state()

        self.assertEqual(state, lib._DEFAULT_STATE)

    def test_bump_counter_recovers_after_corrupt_state_file(self):
        self.state_file.write_text('{"daily_cycles": 3, truncated', encoding="utf-8")

        lib.bump_counter("daily_cycles")

        self.assertEqual(lib.get_counter("daily_cycles"), 1)

    def test_write_state_uses_replace_not_truncating_write(self):
        # Regression guard for the truncate-then-write race: _write_state
        # must never leave STATE_FILE in a state where reading it can see
        # a truncated/partial write. We assert this by checking the write
        # goes through a temp file + os.replace() rather than
        # Path.write_text() directly on STATE_FILE.
        original_replace = os.replace
        replace_calls = []

        def spy_replace(src, dst):
            replace_calls.append((Path(src), Path(dst)))
            # At the moment of replace, the temp file must already hold
            # the full new content (i.e. no truncate-in-place on the
            # real state file).
            self.assertNotEqual(Path(src), self.state_file)
            return original_replace(src, dst)

        with mock.patch("lib.os.replace", side_effect=spy_replace):
            lib.bump_counter("daily_cycles")

        self.assertEqual(len(replace_calls), 1)
        _, dst = replace_calls[0]
        self.assertEqual(dst, self.state_file)
        # No leftover temp file after a successful write.
        leftovers = [p for p in self.tmpdir.iterdir() if p != self.state_file]
        self.assertEqual(leftovers, [])


class LastRunStateTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))
        self.state_file = self.tmpdir / "state.json"
        self.state_file.write_text('{"daily_cycles": 0, "daily_claude_calls": 0, "date": ""}', encoding="utf-8")
        self._patcher = mock.patch.object(lib, "STATE_FILE", self.state_file)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_get_last_run_defaults_to_zero_for_unset_stage(self):
        self.assertEqual(lib.get_last_run("scan"), 0.0)

    def test_set_last_run_round_trips_current_timestamp(self):
        import time as time_module

        before = time_module.time()
        lib.set_last_run("scan")
        after = time_module.time()

        result = lib.get_last_run("scan")
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    def test_set_last_run_only_affects_named_stage(self):
        lib.set_last_run("scan")
        self.assertEqual(lib.get_last_run("tune"), 0.0)


if __name__ == "__main__":
    unittest.main()
