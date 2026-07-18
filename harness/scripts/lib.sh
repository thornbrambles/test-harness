#!/usr/bin/env bash
# Shared helpers sourced by every script.
set -euo pipefail

HARNESS_DIR="${HARNESS_DIR:-.harness}"
LOG_FILE="$HARNESS_DIR/log.jsonl"
STATE_FILE="$HARNESS_DIR/state.json"
HALT_FILE="$HARNESS_DIR/halt.lock"

mkdir -p "$HARNESS_DIR"
[ -f "$STATE_FILE" ] || echo '{"daily_cycles":0,"daily_claude_calls":0,"date":""}' > "$STATE_FILE"

log_event() {
  # log_event <event_type> <issue_number> <json_extra>
  local event="$1" issue="$2" extra="${3:-}"
  [ -n "$extra" ] || extra='{}'
  jq -nc --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg event "$event" \
     --arg issue "$issue" --argjson extra "$extra" \
     '{ts:$ts,event:$event,issue:$issue} + $extra' >> "$LOG_FILE"
}

is_halted() {
  [ -f "$HALT_FILE" ]
}

halt_daemon() {
  local reason="$1"
  echo "$reason" > "$HALT_FILE"
  log_event "halt" "-" "$(jq -nc --arg r "$reason" '{reason:$r}')"
}

reset_daily_counters_if_new_day() {
  local today; today="$(date -u +%Y-%m-%d)"
  local stored; stored="$(jq -r '.date' "$STATE_FILE")"
  if [ "$today" != "$stored" ]; then
    jq --arg d "$today" '.date=$d | .daily_cycles=0 | .daily_claude_calls=0' \
      "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
  fi
}

bump_counter() {
  # bump_counter daily_cycles | daily_claude_calls
  local key="$1"
  jq --arg k "$key" '.[$k] += 1' "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
}

get_counter() {
  jq -r --arg k "$1" '.[$k]' "$STATE_FILE"
}

get_retry_count() {
  # reads retry:N label off an issue, defaults 0
  local issue="$1"
  gh issue view "$issue" --json labels -q '.labels[].name' \
    | grep -o '^retry:[0-9]\+$' | head -1 | cut -d: -f2 || echo 0
}

set_retry_count() {
  local issue="$1" n="$2"
  local old; old="$(get_retry_count "$issue")"
  gh issue edit "$issue" --remove-label "retry:${old:-0}" >/dev/null 2>&1 || true
  gh issue edit "$issue" --add-label "retry:${n}" >/dev/null
}

set_state_label() {
  # set_state_label <issue> <new_state_without_prefix>
  local issue="$1" new="$2"
  for s in triage ready in-progress in-review needs-human; do
    gh issue edit "$issue" --remove-label "state:$s" >/dev/null 2>&1 || true
  done
  gh issue edit "$issue" --add-label "state:$new" >/dev/null
}
