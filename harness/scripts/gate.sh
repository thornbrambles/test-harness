#!/usr/bin/env bash
# Deterministic pre-checks. Exit 0 = pass, 1 = fail.
# Usage: gate.sh <issue_number> <branch> <base_commit>
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"

ISSUE="$1"; BRANCH="$2"; BASE="$3"
FAIL_REASON=""

git fetch origin "$BRANCH" >/dev/null 2>&1 || true
git checkout "$BRANCH" >/dev/null

# 1. forbidden paths
CHANGED_FILES="$(git diff --name-only "$BASE" "$BRANCH")"
if echo "$CHANGED_FILES" | grep -qE "$FORBIDDEN_PATH_REGEX"; then
  FAIL_REASON="touches forbidden path(s): $(echo "$CHANGED_FILES" | grep -E "$FORBIDDEN_PATH_REGEX" | tr '\n' ' ')"
fi

# 2. diff size
LINES_CHANGED="$(git diff --shortstat "$BASE" "$BRANCH" | grep -oE '[0-9]+ insertion|[0-9]+ deletion' | grep -oE '[0-9]+' | paste -sd+ | bc || echo 0)"
if [ -z "$FAIL_REASON" ] && [ "${LINES_CHANGED:-0}" -gt "$MAX_DIFF_LINES" ]; then
  FAIL_REASON="diff too large ($LINES_CHANGED lines > $MAX_DIFF_LINES)"
fi

# 3. no test file touched
if [ -z "$FAIL_REASON" ] && ! echo "$CHANGED_FILES" | grep -qiE 'test|spec'; then
  FAIL_REASON="no test file changed"
fi

# 4. lint/typecheck exit code, if a script exists for it
if [ -z "$FAIL_REASON" ] && [ -f package.json ] && jq -e '.scripts.lint' package.json >/dev/null 2>&1; then
  if ! npm run lint >/tmp/lint_out.txt 2>&1; then
    FAIL_REASON="lint failed: $(tail -n5 /tmp/lint_out.txt | tr '\n' ' ')"
  fi
fi

# 5. oscillation check: same file changed 3+ times across last 3 attempts
#    with < 5 net changed lines each time (i.e. thrashing, not progress)
PRIOR_COMMITS="$(git log --oneline "$BASE".."$BRANCH" | wc -l)"
if [ -z "$FAIL_REASON" ] && [ "$PRIOR_COMMITS" -ge 3 ]; then
  NET_LAST3="$(git diff --shortstat HEAD~3 HEAD 2>/dev/null | grep -oE '[0-9]+' | paste -sd+ | bc || echo 999)"
  if [ "${NET_LAST3:-999}" -lt 5 ]; then
    FAIL_REASON="oscillation detected: last 3 commits show <5 net changed lines"
  fi
fi

if [ -n "$FAIL_REASON" ]; then
  log_event "gate_fail" "$ISSUE" "$(jq -nc --arg r "$FAIL_REASON" '{reason:$r}')"
  echo "GATE FAIL: $FAIL_REASON"
  exit 1
fi

log_event "gate_pass" "$ISSUE" '{}'
echo "GATE PASS"
exit 0
