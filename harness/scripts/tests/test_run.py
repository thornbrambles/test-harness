#!/usr/bin/env python3
"""Tests for run.py's branch-extraction and Tuner-cadence logic (issue #40).

Both were previously inlined in the daemon's `while True` loop with no
direct test, only indirect coverage from other scripts' tests. Pulled out
into extract_branch()/should_run_tuner() here so they can be exercised
without running the daemon loop itself.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import run  # noqa: E402


class ExtractBranchTest(unittest.TestCase):
    def test_returns_last_line_on_success(self):
        result = SimpleNamespace(returncode=0, stdout="cloning...\nbuilding...\nauto/issue-42\n")

        self.assertEqual(run.extract_branch(result), "auto/issue-42")

    def test_ignores_extra_output_before_final_line(self):
        # Regression guard for the exact failure mode issue #40 calls out:
        # an extra print added before build.py's final branch line should
        # not change which line gets picked.
        result = SimpleNamespace(returncode=0, stdout="a\nb\nc\nauto/issue-99")

        self.assertEqual(run.extract_branch(result), "auto/issue-99")

    def test_nonzero_returncode_yields_none(self):
        result = SimpleNamespace(returncode=1, stdout="auto/issue-42\n")

        self.assertIsNone(run.extract_branch(result))

    def test_empty_stdout_yields_none(self):
        result = SimpleNamespace(returncode=0, stdout="")

        self.assertIsNone(run.extract_branch(result))

    def test_whitespace_only_stdout_yields_none(self):
        result = SimpleNamespace(returncode=0, stdout="   \n  \n")

        self.assertIsNone(run.extract_branch(result))


class ShouldRunTunerTest(unittest.TestCase):
    def test_runs_on_multiples_of_cadence(self):
        self.assertTrue(run.should_run_tuner(cycle=20, tuner_every=20))
        self.assertTrue(run.should_run_tuner(cycle=40, tuner_every=20))

    def test_skips_non_multiples(self):
        self.assertFalse(run.should_run_tuner(cycle=19, tuner_every=20))
        self.assertFalse(run.should_run_tuner(cycle=21, tuner_every=20))

    def test_zero_cadence_disables_tuner(self):
        # tuner_every=0 would otherwise raise ZeroDivisionError on `%`;
        # falsy-cadence short-circuits instead of ever running the tuner.
        self.assertFalse(run.should_run_tuner(cycle=0, tuner_every=0))
        self.assertFalse(run.should_run_tuner(cycle=20, tuner_every=0))


if __name__ == "__main__":
    unittest.main()
