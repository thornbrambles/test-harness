# Autonomous Repo Harness

Four agents (Scanner, Builder, Verifier, Tuner) + one deterministic gate +
one Overseer circuit breaker, coordinated through GitHub Issue labels.

## Requirements
- `gh` CLI, authenticated, run from inside the target repo
- `claude` CLI (Claude Code), authenticated
- `jq`

## Setup
```bash
cp config.env.example config.env   # edit thresholds
chmod +x scripts/*.sh
./scripts/run.sh                    # starts the daemon loop
```

To feed in a new feature request at any time:
```bash
gh issue create --title "Add CSV export" --body "..." --label "type:feature,state:ready"
```

## State machine (issue labels)
```
state:triage  -> Scanner filed it, not yet vetted
state:ready   -> queued for Builder
state:in-progress -> Builder actively working
state:in-review   -> Builder done, waiting on gate + Verifier
state:needs-human -> stuck, retries exhausted, or oscillation detected
(closed)      -> Verifier approved, merged
```
Plus a `retry:N` label tracking attempt count per issue.

## Files
- `prompts/*.md` — the actual instructions given to each agent. Only the
  Tuner is allowed to propose edits to these, and only via PR.
- `scripts/gate.sh` — deterministic pre-checks (no LLM). Must pass before
  the Verifier agent is even invoked.
- `scripts/scan.sh`, `build.sh`, `verify.sh`, `tune.sh` — one `claude -p`
  call each, scoped to a single job.
- `scripts/overseer.sh` — reads `.harness/log.jsonl`, enforces global
  thresholds, can write `.harness/halt.lock` to pause everything.
- `scripts/run.sh` — the daemon loop tying it all together.
- `.harness/log.jsonl` — append-only event log every script writes to.
- `.harness/state.json` — running counters (daily cost, backlog size, etc).

## Exit conditions (see config.env)
- `MAX_RETRIES` per issue before `state:needs-human`
- `MAX_OPEN_AUTO_ISSUES` — Scanner stops filing once backlog this large
- `MAX_REJECT_RATE` over trailing N issues — Overseer halts daemon
- `MAX_DAILY_CYCLES` / `MAX_DAILY_CLAUDE_CALLS` — hard daily ceiling
- oscillation check in gate.sh — same file touched by 3+ consecutive
  attempts across an issue's history with no net diff progress
