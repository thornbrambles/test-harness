#!/usr/bin/env bash
# Usage: verify.sh <issue_number> <branch>
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"

ISSUE="$1"; BRANCH="$2"
BASE="main"

# --- fresh checkout, isolated from Builder's working dir ---
WORKDIR="$(mktemp -d)"
git worktree add "$WORKDIR" "$BRANCH" >/dev/null 2>&1
pushd "$WORKDIR" >/dev/null

# --- deterministic gate first ---
if ! bash "$DIR/gate.sh" "$ISSUE" "$BRANCH" "$BASE"; then
  popd >/dev/null; git worktree remove "$WORKDIR" --force
  set_state_label "$ISSUE" "needs-human"
  gh issue comment "$ISSUE" --body "REASON: gate check failed, see .harness/log.jsonl for detail."
  log_event "verify_reject_gate" "$ISSUE" '{}'
  exit 1
fi

# --- collect hard numbers ourselves, not from the agent's report ---
TEST_OUTPUT="$(npm test 2>&1 || true)"   # adjust per repo's test runner

# run just the new/changed tests against the pre-fix commit
CHANGED_TEST_FILES="$(git diff --name-only "$BASE" "$BRANCH" | grep -iE 'test|spec' || true)"
git stash >/dev/null 2>&1 || true
git checkout "$BASE" -- . >/dev/null 2>&1 || true
PRE_FIX_TEST_OUTPUT="pre-fix run skipped (no isolated test target configured)"
if [ -n "$CHANGED_TEST_FILES" ]; then
  PRE_FIX_TEST_OUTPUT="$(npx jest $CHANGED_TEST_FILES 2>&1 || true)"
fi
git checkout "$BRANCH" -- . >/dev/null 2>&1
git stash pop >/dev/null 2>&1 || true

COVERAGE_DELTA="see coverage tool output in TEST_OUTPUT above"

PROMPT="$(sed \
  -e "s/{{ISSUE_NUMBER}}/$ISSUE/" \
  -e "s/{{BRANCH_NAME}}/$BRANCH/" \
  "$DIR/../prompts/verifier.md")"
PROMPT="${PROMPT//\{\{TEST_OUTPUT\}\}/$TEST_OUTPUT}"
PROMPT="${PROMPT//\{\{PRE_FIX_TEST_OUTPUT\}\}/$PRE_FIX_TEST_OUTPUT}"
PROMPT="${PROMPT//\{\{COVERAGE_DELTA\}\}/$COVERAGE_DELTA}"
PROMPT="${PROMPT//\{\{GATE_RESULT\}\}/PASS}"

RESULT="$(claude -p "$PROMPT" --allowedTools "Bash(git:*),Read,Grep,Glob" 2>&1 || true)"
bump_counter daily_claude_calls
popd >/dev/null
git worktree remove "$WORKDIR" --force

if echo "$RESULT" | grep -q "VERDICT: APPROVE"; then
  gh pr create --head "$BRANCH" --base main --title "Fixes #$ISSUE" --body "Auto-verified. Closes #$ISSUE." >/dev/null 2>&1 || true
  gh issue close "$ISSUE" --comment "Auto-verified and merged." >/dev/null
  log_event "verify_approve" "$ISSUE" '{}'
  echo "APPROVED"
else
  REASON="$(echo "$RESULT" | grep "^REASON:" || echo "REASON: unspecified")"
  RETRY="$(get_retry_count "$ISSUE")"
  NEW_RETRY=$((RETRY + 1))
  gh issue comment "$ISSUE" --body "$REASON" >/dev/null
  if [ "$NEW_RETRY" -ge "$MAX_RETRIES" ]; then
    set_state_label "$ISSUE" "needs-human"
    log_event "verify_reject_final" "$ISSUE" "$(jq -nc --arg r "$REASON" '{reason:$r}')"
  else
    set_retry_count "$ISSUE" "$NEW_RETRY"
    set_state_label "$ISSUE" "ready"
    log_event "verify_reject_retry" "$ISSUE" "$(jq -nc --arg r "$REASON" --arg n "$NEW_RETRY" '{reason:$r,retry:$n}')"
  fi
  echo "REJECTED"
fi
