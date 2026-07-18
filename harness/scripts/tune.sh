#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"

if is_halted; then echo "Halted: $(cat "$HALT_FILE")"; exit 0; fi

LOOKBACK=200
LOG_TAIL="$(tail -n "$LOOKBACK" "$LOG_FILE" 2>/dev/null || echo "[]")"

PROMPT="$(sed -e "s/{{LOOKBACK}}/$LOOKBACK/" "$DIR/../prompts/tuner.md")"
PROMPT="$PROMPT

LOG DATA:
$LOG_TAIL"

BRANCH="tuner/$(date -u +%Y%m%d-%H%M%S)"
git checkout -B "$BRANCH" main >/dev/null 2>&1

claude -p "$PROMPT" --allowedTools "Bash(git:*),Bash(gh:*),Read,Write,Edit" > /tmp/tune_out.txt 2>&1 || true
bump_counter daily_claude_calls

if ! git diff --quiet main -- prompts/ config.env 2>/dev/null; then
  git push -u origin "$BRANCH" >/dev/null 2>&1 || true
  gh pr create --head "$BRANCH" --base main --title "Tuner: prompt/config refinement $(date -u +%Y-%m-%d)" \
    --body "Automated proposal from Tuner agent. See /tmp/tune_out.txt for reasoning. Requires human review before merge." >/dev/null 2>&1 || true
  log_event "tune_pr_opened" "-" "$(jq -nc --arg b "$BRANCH" '{branch:$b}')"
else
  log_event "tune_no_change" "-" '{}'
fi
git checkout main >/dev/null 2>&1
