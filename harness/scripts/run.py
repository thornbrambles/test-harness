#!/usr/bin/env python3
"""Daemon loop tying scan/build/verify/tune/overseer together. Usage: run.py"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import lib

SCRIPTS_DIR = Path(__file__).parent


def run_stage(name: str, *args: str) -> subprocess.CompletedProcess:
    """Runs a stage as a subprocess with output streamed live (not captured),
    matching how the bash daemon let each sub-script print straight through."""
    return subprocess.run([sys.executable, str(SCRIPTS_DIR / name), *args], text=True)


def extract_branch(build_result: subprocess.CompletedProcess) -> str | None:
    """Parses build.py's captured stdout for the branch name it prints as
    its final line -- only trustworthy when the subprocess succeeded and
    actually printed something."""
    lines = build_result.stdout.strip().splitlines()
    return lines[-1] if build_result.returncode == 0 and lines else None


def should_run_tuner(cycle: int, tuner_every: int) -> bool:
    return bool(tuner_every) and cycle % tuner_every == 0


def main() -> int:
    config = lib.load_config(Path(__file__).parent.parent / "config.env")
    cycle = 0

    while True:
        if lib.is_halted():
            print(f"Daemon halted: {lib.HALT_FILE.read_text(encoding='utf-8')}")
            print(f"Fix the issue, then: rm {lib.HALT_FILE}")
            return 1

        run_stage("overseer.py")
        if lib.is_halted():
            continue

        lib.reset_daily_counters_if_new_day()
        lib.bump_counter("daily_cycles")
        cycle += 1

        print(f"=== cycle {cycle}: scan ===")
        run_stage("scan.py")

        print(f"=== cycle {cycle}: triage ===")
        run_stage("triage.py")

        print(f"=== cycle {cycle}: work ===")
        issue_result = lib.run([
            "gh", "issue", "list", "--state", "open", "--label", "state:ready",
            "--limit", "1", "--json", "number", "-q", ".[0].number",
        ])
        issue = issue_result.stdout.strip()
        if issue and issue != "null":
            # Captured (not streamed) like the old run.sh, which relied on
            # command substitution to grab build.sh's final printed line.
            build_result = lib.run([sys.executable, str(SCRIPTS_DIR / "build.py"), issue])
            print(build_result.stdout + build_result.stderr)
            branch = extract_branch(build_result)
            if branch:
                run_stage("verify.py", issue, branch)
        else:
            print("no ready issues")

        tuner_every = lib.cfg_int(config, "TUNER_EVERY_N_CYCLES")
        if should_run_tuner(cycle, tuner_every):
            print(f"=== cycle {cycle}: tune ===")
            run_stage("tune.py")

        time.sleep(lib.cfg_int(config, "SCAN_INTERVAL_SECONDS"))


if __name__ == "__main__":
    sys.exit(main())
