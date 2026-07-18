#!/usr/bin/env bash
# Usage: build.sh <issue_number>
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"

ISSUE="$1"
RETRY="$(get_retry_count "$ISSUE")"
BRANCH="auto/issue-$ISSUE"
ISSUE_BODY="$(gh issue view "$ISSUE" --json body -q .body)"

PRIOR_FEEDBACK=""
if [ "$RETRY" -gt 0 ]; then
  PRIOR_FEEDBACK="$(gh issue view "$ISSUE" --json comments \
    -q '.comments[] | select(.body | startswith("REASON:")) | .body' | tail -3)"
fi

if [ "$RETRY" -gt 0 ]; then
  # Retries continue on the existing branch so commits accumulate across
  # attempts (needed for the gate.sh oscillation check). Only fall back to
  # resetting from main if the branch doesn't exist locally yet.
  git checkout "$BRANCH" >/dev/null 2>&1 || git checkout -B "$BRANCH" main >/dev/null
else
  git checkout -B "$BRANCH" main >/dev/null
fi

# Static substitutions via sed, dynamic ones via bash string replace below.
PROMPT="$(sed \
  -e "s/{{ISSUE_NUMBER}}/$ISSUE/" \
  -e "s#{{BRANCH_NAME}}#$BRANCH#" \
  -e "s/{{RETRY_COUNT}}/$RETRY/" \
  -e "s#{{FORBIDDEN_PATH_REGEX}}#$FORBIDDEN_PATH_REGEX#" \
  "$DIR/../prompts/builder.md")"
PROMPT="${PROMPT//\{\{ISSUE_BODY\}\}/$ISSUE_BODY}"
if [ -n "$PRIOR_FEEDBACK" ]; then
  PROMPT="${PROMPT//\{\{#PRIOR_FEEDBACK\}\}/}"
  PROMPT="${PROMPT//\{\{\/PRIOR_FEEDBACK\}\}/}"
  PROMPT="${PROMPT//\{\{PRIOR_FEEDBACK\}\}/$PRIOR_FEEDBACK}"
else
  PROMPT="$(echo "$PROMPT" | sed '/{{#PRIOR_FEEDBACK}}/,/{{\/PRIOR_FEEDBACK}}/d')"
fi

set_state_label "$ISSUE" "in-progress"
claude -p "$PROMPT" --allowedTools "Bash,Read,Write,Edit,Grep,Glob" > /tmp/build_out.txt 2>&1 || true
bump_counter daily_claude_calls

git push -u origin "$BRANCH" >/dev/null 2>&1 || true
set_state_label "$ISSUE" "in-review"
log_event "build_complete" "$ISSUE" "$(jq -nc --arg b "$BRANCH" --arg r "$RETRY" '{branch:$b,retry:$r}')"
echo "$BRANCH"
