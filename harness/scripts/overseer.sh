#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"

reset_daily_counters_if_new_day

# 1. daily ceilings
if [ "$(get_counter daily_cycles)" -ge "$MAX_DAILY_CYCLES" ]; then
  halt_daemon "daily cycle cap reached ($MAX_DAILY_CYCLES)"; exit 0
fi
if [ "$(get_counter daily_claude_calls)" -ge "$MAX_DAILY_CLAUDE_CALLS" ]; then
  halt_daemon "daily claude call cap reached ($MAX_DAILY_CLAUDE_CALLS)"; exit 0
fi

# 2. trailing reject rate
RECENT="$(tail -n 500 "$LOG_FILE" 2>/dev/null | jq -c 'select(.event=="verify_approve" or .event|test("verify_reject"))' | tail -n "$REJECT_RATE_WINDOW")"
TOTAL="$(echo "$RECENT" | grep -c . || true)"
REJECTS="$(echo "$RECENT" | grep -c "verify_reject" || true)"
if [ "$TOTAL" -ge "$REJECT_RATE_WINDOW" ]; then
  RATE="$(echo "scale=2; $REJECTS / $TOTAL" | bc)"
  if (( $(echo "$RATE > $MAX_REJECT_RATE" | bc -l) )); then
    halt_daemon "reject rate $RATE over last $TOTAL verdicts exceeds $MAX_REJECT_RATE"; exit 0
  fi
fi

# 3. any human-reopened issue after auto-close
if [ "$HALT_ON_HUMAN_REOPEN" = "true" ]; then
  REOPENED="$(gh issue list --state open --label "type:auto-detected" --search "reopened-by:@me is:open" --json number -q length 2>/dev/null || echo 0)"
  # Simpler reliable signal: issues with a 'closed' + later 'reopened' event pair in gh's own timeline
  # left as a TODO hook — gh's search syntax for this is limited; consider a webhook instead.
  true
fi

log_event "overseer_check" "-" "$(jq -nc --arg t "$TOTAL" --arg r "$REJECTS" '{window:$t,rejects:$r}')"
echo "OK"
