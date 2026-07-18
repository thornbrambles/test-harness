#!/usr/bin/env python3
"""Builder agent driver. Usage: build.py <issue_number>

Prints the branch name on success (matching the old build.sh contract so
run.py can capture it the same way).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import lib

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_COND_BLOCK_RE = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.DOTALL)


def render(template: str, values: dict[str, str]) -> str:
    """Mustache-lite: {{KEY}} substitution plus {{#KEY}}...{{/KEY}} blocks
    that are kept (with the KEY substituted inside) when values[KEY] is
    truthy, and dropped entirely otherwise."""

    def _cond(match: re.Match) -> str:
        key, body = match.group(1), match.group(2)
        if values.get(key):
            return body
        return ""

    out = _COND_BLOCK_RE.sub(_cond, template)
    for key, value in values.items():
        out = out.replace(f"{{{{{key}}}}}", value)
    return out


def get_prior_feedback(issue) -> str:
    result = lib.run(["gh", "issue", "view", str(issue), "--json", "comments"])
    comments = json.loads(result.stdout).get("comments", [])
    reasons = [c["body"] for c in comments if c["body"].startswith("REASON:")]
    return "\n".join(reasons[-3:])


def main() -> int:
    issue = sys.argv[1]
    config = lib.load_config(Path(__file__).parent.parent / "config.env")

    retry = lib.get_retry_count(issue)
    branch = f"auto/issue-{issue}"
    issue_body = lib.run(["gh", "issue", "view", str(issue), "--json", "body", "-q", ".body"]).stdout

    prior_feedback = get_prior_feedback(issue) if retry > 0 else ""

    if retry > 0:
        # Retries continue on the existing branch so commits accumulate
        # across attempts (needed for gate.py's oscillation check). Only
        # fall back to resetting from main if the branch doesn't exist yet.
        if lib.run(["git", "checkout", branch]).returncode != 0:
            lib.run(["git", "checkout", "-B", branch, "main"])
    else:
        lib.run(["git", "checkout", "-B", branch, "main"])

    template = PROMPTS_DIR.joinpath("builder.md").read_text(encoding="utf-8")
    prompt = render(
        template,
        {
            "ISSUE_NUMBER": issue,
            "BRANCH_NAME": branch,
            "RETRY_COUNT": str(retry),
            "FORBIDDEN_PATH_REGEX": config["FORBIDDEN_PATH_REGEX"],
            "ISSUE_BODY": issue_body,
            "PRIOR_FEEDBACK": prior_feedback,
        },
    )

    lib.set_state_label(issue, "in-progress")
    lib.run(["claude", "-p", prompt, "--allowedTools", "Bash,Read,Write,Edit,Grep,Glob"])
    lib.bump_counter("daily_claude_calls")

    # Safety net: the Builder is instructed to push+PR itself now, but if it
    # didn't, make sure the branch is at least on origin.
    lib.run(["git", "push", "-u", "origin", branch])

    lib.set_state_label(issue, "in-review")
    lib.log_event("build_complete", issue, {"branch": branch, "retry": str(retry)})
    print(branch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
