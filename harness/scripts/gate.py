#!/usr/bin/env python3
"""Deterministic pre-checks. No LLM calls. Usage: gate.py <issue> <branch> <base>

Returns (passed, reason) from check_gate() for import by verify.py, or
prints GATE PASS / GATE FAIL: <reason> and exits 0/1 when run standalone.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import lib


def _numstat_total(base: str, ref: str) -> int:
    """Sum of insertions+deletions between base and ref, via numstat (machine-
    readable, unlike parsing the `n insertions(+), m deletions(-)` text the
    bash version scraped with grep)."""
    result = lib.run(["git", "diff", "--numstat", base, ref])
    total = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        added, deleted = parts[0], parts[1]
        # binary files report "-" for both columns
        total += int(added) if added.isdigit() else 0
        total += int(deleted) if deleted.isdigit() else 0
    return total


def check_gate(issue, branch: str, base: str, config: dict) -> tuple[bool, str]:
    lib.run(["git", "fetch", "origin", branch])
    lib.run(["git", "checkout", branch])

    changed = lib.run(["git", "diff", "--name-only", base, branch]).stdout.splitlines()
    changed = [f for f in changed if f]

    # 1. forbidden paths
    forbidden_re = re.compile(config["FORBIDDEN_PATH_REGEX"])
    forbidden_hits = [f for f in changed if forbidden_re.search(f)]
    if forbidden_hits:
        return False, f"touches forbidden path(s): {' '.join(forbidden_hits)}"

    # 2. diff size
    lines_changed = _numstat_total(base, branch)
    if lines_changed > lib.cfg_int(config, "MAX_DIFF_LINES"):
        return False, f"diff too large ({lines_changed} lines > {config['MAX_DIFF_LINES']})"

    # 3. no test file touched
    if not any(lib.is_test_file(f) for f in changed):
        return False, "no test file changed"

    # 4. lint, if a script exists for it
    pkg = Path("package.json")
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if "lint" in scripts:
            lint = lib.run(["npm", "run", "lint"])
            if lint.returncode != 0:
                tail = "\n".join((lint.stdout + lint.stderr).splitlines()[-5:])
                return False, f"lint failed: {tail}"

    # 5. oscillation: same file(s) thrashing across 3+ consecutive attempts
    #    with <5 net changed lines each time (no real progress)
    prior_commits = int(lib.run(["git", "rev-list", "--count", f"{base}..{branch}"]).stdout.strip() or "0")
    if prior_commits >= 3:
        net_last3 = _numstat_total("HEAD~3", "HEAD")
        if net_last3 < 5:
            return False, "oscillation detected: last 3 commits show <5 net changed lines"

    return True, ""


def main() -> int:
    issue, branch, base = sys.argv[1], sys.argv[2], sys.argv[3]
    config = lib.load_config(Path(__file__).parent.parent / "config.env")
    passed, reason = check_gate(issue, branch, base, config)
    if not passed:
        lib.log_event("gate_fail", issue, {"reason": reason})
        print(f"GATE FAIL: {reason}")
        return 1
    lib.log_event("gate_pass", issue, {})
    print("GATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
