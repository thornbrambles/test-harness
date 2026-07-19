#!/usr/bin/env python3
"""Tests for lib.is_test_file (issue #7).

gate.py and verify.py used to each detect "is this a test file" with
re.compile(r"test|spec", re.IGNORECASE).search() against the full path --
an unanchored substring match that also fires on ordinary files like
src/latest.py, contest_winners.py, attestation.py (contain "test") or
docs/specification.md, respected.js (contain "spec"). That let gate.py's
"no test file changed" check be spuriously satisfied by non-test changes,
and made verify.py dispatch non-test files through the pre-fix test runner
(e.g. via npx jest) as if they were real tests. lib.is_test_file() replaces
both call sites with a single pattern anchored to actual test-file naming
conventions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
