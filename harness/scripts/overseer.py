#!/usr/bin/env python3
"""Circuit breaker. Reads .harness/log.jsonl, enforces global thresholds,
can write .harness/halt.lock to pause everything. Usage: overseer.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lib


def _tail_log(n: int) -> list[dict]:
    if not lib.LOG_FILE.exists():
        return []
    lines = lib.LOG_FILE.read_text(encoding="utf-8").splitlines()[-n:]
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def check_reject_rate(config: dict) -> tuple[bool, str]:
    window = lib.cfg_int(config, "REJECT_RATE_WINDOW")
    recent = [
        e for e in _tail_log(500)
        if e.get("event") == "verify_approve" or "verify_reject" in e.get("event", "")
    ][-window:]
    total = len(recent)
    rejects = sum(1 for e in recent if "verify_reject" in e.get("event", ""))
    if total >= window:
        rate = rejects / total
        if rate > float(config["MAX_REJECT_RATE"]):
            return False, f"reject rate {rate:.2f} over last {total} verdicts exceeds {config['MAX_REJECT_RATE']}"
    return True, ""


def check_human_reopen() -> tuple[bool, str]:
    """The bash version left this as a documented TODO/no-op despite
    HALT_ON_HUMAN_REOPEN=true in config. Implemented here: any issue we
    auto-closed (verify_approve) that's open again means a human reopened
    it, which should halt the daemon for review."""
    auto_closed_issues = {e["issue"] for e in _tail_log(500) if e.get("event") == "verify_approve"}
    for issue in auto_closed_issues:
        if issue == "-":
            continue
        result = lib.run(["gh", "issue", "view", issue, "--json", "state", "-q", ".state"])
        if result.returncode == 0 and result.stdout.strip() == "OPEN":
            return False, f"issue #{issue} was auto-closed but is now reopened"
    return True, ""


def main() -> int:
    config = lib.load_config(Path(__file__).parent.parent / "config.env")
    lib.reset_daily_counters_if_new_day()

    if lib.get_counter("daily_cycles") >= lib.cfg_int(config, "MAX_DAILY_CYCLES"):
        lib.halt_daemon(f"daily cycle cap reached ({config['MAX_DAILY_CYCLES']})")
        return 0
    if lib.get_counter("daily_claude_calls") >= lib.cfg_int(config, "MAX_DAILY_CLAUDE_CALLS"):
        lib.halt_daemon(f"daily claude call cap reached ({config['MAX_DAILY_CLAUDE_CALLS']})")
        return 0

    ok, reason = check_reject_rate(config)
    if not ok:
        lib.halt_daemon(reason)
        return 0

    if lib.cfg_bool(config, "HALT_ON_HUMAN_REOPEN"):
        ok, reason = check_human_reopen()
        if not ok:
            lib.halt_daemon(reason)
            return 0

    lib.log_event("overseer_check", "-", {})
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
