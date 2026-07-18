#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib.sh"
source "$DIR/../config.env"
cd "$REPO_DIR"

CYCLE=0

while true; do
  if is_halted; then
    echo "Daemon halted: $(cat "$HALT_FILE")"
    echo "Fix the issue, then: rm $HALT_FILE"
    exit 1
  fi

  bash "$DIR/overseer.sh" || true
  if is_halted; then continue; fi

  reset_daily_counters_if_new_day
  bump_counter daily_cycles
  CYCLE=$((CYCLE + 1))

  echo "=== cycle $CYCLE: scan ==="
  bash "$DIR/scan.sh" || true

  echo "=== cycle $CYCLE: work ==="
  ISSUE="$(gh issue list --state open --label "state:ready" --limit 1 --json number -q '.[0].number' || true)"
  if [ -n "${ISSUE:-}" ] && [ "$ISSUE" != "null" ]; then
    BRANCH="$(bash "$DIR/build.sh" "$ISSUE")"
    bash "$DIR/verify.sh" "$ISSUE" "$BRANCH" || true
  else
    echo "no ready issues"
  fi

  if [ $((CYCLE % TUNER_EVERY_N_CYCLES)) -eq 0 ]; then
    echo "=== cycle $CYCLE: tune ==="
    bash "$DIR/tune.sh" || true
  fi

  sleep "$SCAN_INTERVAL_SECONDS"
done
