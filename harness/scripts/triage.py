#!/usr/bin/env python3
"""Triager agent driver. Classifies open state:triage issues by risk and
promotes low-risk ones to state:ready. Usage: triage.py"""
from __future__ import annotations

import sys
from pathlib import Path

import lib

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def main() -> int:
    if lib.is_halted():
        print(f"Halted: {lib.HALT_FILE.read_text(encoding='utf-8')}")
        return 0

    prompt = PROMPTS_DIR.joinpath("triager.md").read_text(encoding="utf-8")

    result = lib.run(
        ["claude", "-p", prompt, "--allowedTools", "Bash(gh:*),Read,Grep,Glob"]
    )
    lib.bump_counter("daily_claude_calls")
    lib.log_event("triage_complete", "-", {})

    print(result.stdout + result.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
