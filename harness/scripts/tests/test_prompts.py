#!/usr/bin/env python3
"""Tests for prompts/tuner.md (issue #60).

The harness was ported from bash to Python (commit b0d3aa2), but
prompts/tuner.md still referenced the old gate.sh filename when describing
forbidden-path rejections. Since prompts/tuner.md is fed directly into the
Tuner agent's prompt, a stale filename risks the agent citing a nonexistent
script when reasoning about log evidence. The check now lives in
scripts/gate.py's check_gate().
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class TunerPromptTest(unittest.TestCase):
    def test_no_stale_gate_sh_reference(self):
        text = (PROMPTS_DIR / "tuner.md").read_text(encoding="utf-8")
        self.assertNotIn("gate.sh", text)
        self.assertIn("gate.py", text)


if __name__ == "__main__":
    unittest.main()
