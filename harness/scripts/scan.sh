#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"

if is_halted; then echo "Halted: $(cat "$HALT_FILE")"; exit 0; fi

OPEN_AUTO="$(gh issue list --state open --label type:auto-detected --json number -q length)"

PROMPT="$(sed \
  -e "s/{{MAX_ISSUES_PER_SCAN}}/$MAX_ISSUES_PER_SCAN/" \
  -e "s/{{MAX_OPEN_AUTO_ISSUES}}/$MAX_OPEN_AUTO_ISSUES/" \
  "$DIR/../prompts/scanner.md")"

claude -p "$PROMPT" --allowedTools "Bash(gh:*),Bash(git:*),Read,Grep,Glob" > /tmp/scan_out.txt 2>&1 || true
bump_counter daily_claude_calls

log_event "scan_complete" "-" "$(jq -nc --arg open "$OPEN_AUTO" '{open_auto_before:$open}')"
cat /tmp/scan_out.txt
