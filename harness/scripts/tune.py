#!/usr/bin/env python3
"""Tuner agent driver. Proposes prompt/config edits via PR. Usage: tune.py"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import lib

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
LOOKBACK = 200


def main() -> int:
    if lib.is_halted():
        print(f"Halted: {lib.HALT_FILE.read_text(encoding='utf-8')}")
        return 0

    log_tail = ""
    if lib.LOG_FILE.exists():
        log_tail = "\n".join(lib.LOG_FILE.read_text(encoding="utf-8").splitlines()[-LOOKBACK:])

    template = PROMPTS_DIR.joinpath("tuner.md").read_text(encoding="utf-8")
    prompt = template.replace("{{LOOKBACK}}", str(LOOKBACK))
    prompt += f"\n\nLOG DATA:\n{log_tail}"

    branch = f"tuner/{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    checkout = lib.run(["git", "checkout", "-B", branch, "main"])
    if checkout.returncode != 0:
        detail = (checkout.stderr or checkout.stdout or "").strip()
        lib.log_event("tune_checkout_failed", "-", {"branch": branch, "detail": detail[-2000:]})
        return 1

    lib.run(["claude", "-p", prompt, "--allowedTools", "Bash(git:*),Bash(gh:*),Read,Write,Edit"])
    lib.bump_counter("daily_claude_calls")

    diff = lib.run(["git", "diff", "--quiet", "main", "--", "prompts/", "config.env"])
    if diff.returncode != 0:
        lib.run(["git", "push", "-u", "origin", branch])
        lib.run([
            "gh", "pr", "create", "--head", branch, "--base", "main",
            "--title", f"Tuner: prompt/config refinement {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "--body", "Automated proposal from Tuner agent. Requires human review before merge.",
        ])
        lib.log_event("tune_pr_opened", "-", {"branch": branch})
    else:
        lib.log_event("tune_no_change", "-", {})

    lib.run(["git", "checkout", "main"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
