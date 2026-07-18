#!/usr/bin/env python3
"""Scanner agent driver. Usage: scan.py"""
from __future__ import annotations

import sys
from pathlib import Path

import lib

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def main() -> int:
    if lib.is_halted():
        print(f"Halted: {lib.HALT_FILE.read_text(encoding='utf-8')}")
        return 0

    config = lib.load_config(Path(__file__).parent.parent / "config.env")

    open_auto = lib.run(
        ["gh", "issue", "list", "--state", "open", "--label", "type:auto-detected", "--json", "number", "-q", "length"]
    ).stdout.strip()

    prompt = PROMPTS_DIR.joinpath("scanner.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{{MAX_ISSUES_PER_SCAN}}", config["MAX_ISSUES_PER_SCAN"])
    prompt = prompt.replace("{{MAX_OPEN_AUTO_ISSUES}}", config["MAX_OPEN_AUTO_ISSUES"])

    result = lib.run(
        ["claude", "-p", prompt, "--allowedTools", "Bash(gh:*),Bash(git:*),Read,Grep,Glob"]
    )
    lib.bump_counter("daily_claude_calls")
    lib.log_event("scan_complete", "-", {"open_auto_before": open_auto})

    print(result.stdout + result.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
