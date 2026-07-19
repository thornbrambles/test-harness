#!/usr/bin/env python3
"""Daemon loop tying scan/triage/build/verify/tune together. Each stage
runs on its own configurable interval (SCAN_INTERVAL_SECONDS,
TRIAGE_INTERVAL_SECONDS, BUILD_INTERVAL_SECONDS, TUNER_INTERVAL_SECONDS)
rather than all being gated behind one shared per-iteration cadence -- the
loop wakes on a short LOOP_TICK_SECONDS heartbeat and checks which stages
are actually due. Usage: run.py"""
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


def due(stage: str, interval_seconds: int) -> bool:
    """A stage is due once its own interval has elapsed since its own last
    run. Last-run times are persisted in .harness/state.json (via
    lib.get_last_run/set_last_run), not an in-process counter -- so cadence
    survives a daemon restart (see issue #37: the old Tuner cycle counter
    didn't)."""
    if interval_seconds <= 0:
        return False
    return (time.time() - lib.get_last_run(stage)) >= interval_seconds


def main() -> int:
    config_path = Path(__file__).parent.parent / "config.env"

    while True:
        config = lib.load_config(config_path)

        if lib.is_halted():
            print(f"Daemon halted: {lib.HALT_FILE.read_text(encoding='utf-8')}")
            print(f"Fix the issue, then: rm {lib.HALT_FILE}")
            return 1

        run_stage("overseer.py")
        if lib.is_halted():
            continue

        lib.reset_daily_counters_if_new_day()
        lib.bump_counter("daily_cycles")

        if due("scan", lib.cfg_int(config, "SCAN_INTERVAL_SECONDS")):
            print("=== scan ===")
            run_stage("scan.py")
            lib.set_last_run("scan")

        if due("triage", lib.cfg_int(config, "TRIAGE_INTERVAL_SECONDS")):
            print("=== triage ===")
            run_stage("triage.py")
            lib.set_last_run("triage")

        if due("build", lib.cfg_int(config, "BUILD_INTERVAL_SECONDS")):
            issue_result = lib.run([
                "gh", "issue", "list", "--state", "open", "--label", "state:ready",
                "--limit", "1", "--json", "number", "-q", ".[0].number",
            ])
            issue = issue_result.stdout.strip()
            if issue and issue != "null":
                print(f"=== work: issue #{issue} ===")
                # Captured (not streamed) like the old run.sh, which relied on
                # command substitution to grab build.sh's final printed line.
                build_result = lib.run([sys.executable, str(SCRIPTS_DIR / "build.py"), issue])
                print(build_result.stdout + build_result.stderr)
                branch = extract_branch(build_result)
                if branch:
                    run_stage("verify.py", issue, branch)
            else:
                print("no ready issues")
            lib.set_last_run("build")

        if due("tune", lib.cfg_int(config, "TUNER_INTERVAL_SECONDS")):
            print("=== tune ===")
            run_stage("tune.py")
            lib.set_last_run("tune")

        time.sleep(lib.cfg_int(config, "LOOP_TICK_SECONDS"))


if __name__ == "__main__":
    sys.exit(main())
