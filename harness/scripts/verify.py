#!/usr/bin/env python3
"""Verifier agent driver. Usage: verify.py <issue_number> <branch>"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import gate
import lib

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
TEST_FILE_RE = re.compile(r"test|spec", re.IGNORECASE)


def detect_test_cmd() -> list[str] | None:
    """Prefer an npm "test" script if present, else fall back to a
    run_tests.sh entrypoint (searched a few levels deep to allow a nested
    harness/ layout), instead of assuming npm/jest like the old script did."""
    pkg = Path("package.json")
    if pkg.exists():
        try:
            if "test" in json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}):
                return ["npm", "test"]
        except json.JSONDecodeError:
            pass
    for candidate in Path(".").glob("**/run_tests.sh"):
        parts = candidate.parts
        if "node_modules" in parts or ".git" in parts:
            continue
        if len(parts) <= 4:
            return ["bash", str(candidate)]
    return None


def run_test_cmd(cmd: list[str] | None) -> str:
    if cmd is None:
        return "no test runner detected (checked package.json scripts.test and run_tests.sh)"
    result = lib.run(cmd)
    return result.stdout + result.stderr


def changed_test_files(base: str, branch: str) -> list[str]:
    names = lib.run(["git", "diff", "--name-only", base, branch]).stdout.splitlines()
    return [f for f in names if f and TEST_FILE_RE.search(f)]


def pre_fix_test_output(base: str, branch: str, test_files: list[str]) -> str:
    """Revert source to the pre-fix commit while keeping the new/changed test
    files at their post-fix content, then run just those test files. (The
    old bash version reverted the test files too via `checkout base -- .`,
    which made this check meaningless for anything but a brand-new test
    file -- see issue #2.)"""
    if not test_files:
        return "pre-fix run skipped (no isolated test target configured)"

    lib.run(["git", "checkout", base, "--", "."])
    lib.run(["git", "checkout", branch, "--", *test_files])

    chunks = []
    for f in test_files:
        if not Path(f).is_file():
            continue
        if f.endswith(".sh"):
            result = lib.run(["bash", f])
        else:
            result = lib.run(["npx", "jest", f])
        chunks.append(f"--- {f} ---\n{result.stdout + result.stderr}")

    lib.run(["git", "checkout", branch, "--", "."])
    return "\n".join(chunks)


def main() -> int:
    issue, branch = sys.argv[1], sys.argv[2]
    base = "main"
    config = lib.load_config(Path(__file__).parent.parent / "config.env")

    workdir = tempfile.mkdtemp()
    lib.run(["git", "worktree", "add", workdir, branch])

    old_cwd = os.getcwd()
    os.chdir(workdir)
    gate_passed = True
    gate_reason = ""
    verdict_text = ""
    try:
        gate_passed, gate_reason = gate.check_gate(issue, branch, base, config)
        if gate_passed:
            test_output = run_test_cmd(detect_test_cmd())
            test_files = changed_test_files(base, branch)
            pre_fix_output = pre_fix_test_output(base, branch, test_files)
            coverage_delta = "see coverage tool output in TEST_OUTPUT above"

            template = PROMPTS_DIR.joinpath("verifier.md").read_text(encoding="utf-8")
            prompt = (
                template.replace("{{ISSUE_NUMBER}}", str(issue))
                .replace("{{BRANCH_NAME}}", branch)
                .replace("{{TEST_OUTPUT}}", test_output)
                .replace("{{PRE_FIX_TEST_OUTPUT}}", pre_fix_output)
                .replace("{{COVERAGE_DELTA}}", coverage_delta)
                .replace("{{GATE_RESULT}}", "PASS")
            )

            result = lib.run(["claude", "-p", prompt, "--allowedTools", "Bash(git:*),Read,Grep,Glob"])
            lib.bump_counter("daily_claude_calls")
            verdict_text = result.stdout + result.stderr
    finally:
        os.chdir(old_cwd)
        removed = lib.run(["git", "worktree", "remove", workdir, "--force"])
        if removed.returncode != 0:
            shutil.rmtree(workdir, ignore_errors=True)

    if not gate_passed:
        lib.set_state_label(issue, "needs-human")
        lib.run(["gh", "issue", "comment", str(issue), "--body",
                  "REASON: gate check failed, see .harness/log.jsonl for detail."])
        lib.log_event("verify_reject_gate", issue, {})
        print("REJECTED")
        return 1

    if "VERDICT: APPROVE" in verdict_text:
        pr_num = lib.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "-q", ".[0].number"]
        ).stdout.strip()
        if not pr_num or pr_num == "null":
            lib.run(["gh", "pr", "create", "--head", branch, "--base", "main",
                      "--title", f"Fixes #{issue}", "--body", f"Auto-verified. Closes #{issue}."])
        else:
            lib.run(["gh", "pr", "comment", pr_num, "--body", "Auto-verified by the Verifier agent."])
        lib.run(["gh", "pr", "merge", branch, "--squash", "--delete-branch"])
        lib.run(["gh", "issue", "close", str(issue), "--comment", "Auto-verified and merged."])
        lib.log_event("verify_approve", issue, {})
        print("APPROVED")
        return 0

    reason_match = re.search(r"^REASON:.*$", verdict_text, re.MULTILINE)
    reason = reason_match.group(0) if reason_match else "REASON: unspecified"
    retry = lib.get_retry_count(issue)
    new_retry = retry + 1
    lib.run(["gh", "issue", "comment", str(issue), "--body", reason])
    if new_retry >= lib.cfg_int(config, "MAX_RETRIES"):
        lib.set_state_label(issue, "needs-human")
        lib.log_event("verify_reject_final", issue, {"reason": reason})
    else:
        lib.set_retry_count(issue, new_retry)
        lib.set_state_label(issue, "ready")
        lib.log_event("verify_reject_retry", issue, {"reason": reason, "retry": str(new_retry)})
    print("REJECTED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
