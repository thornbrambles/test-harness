# Autonomous Repo Harness

Four agents (Scanner, Builder, Verifier, Tuner) + one deterministic gate +
one Overseer circuit breaker, coordinated through GitHub Issue labels.

## Requirements
- Python 3.10+
- `gh` CLI, authenticated, run from inside the target repo
- `claude` CLI (Claude Code), authenticated

## Setup
```bash
cp config.env.example config.env   # edit thresholds
python3 scripts/run.py             # starts the daemon loop
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
Plus a `retry:N` label tracking attempt count per issue, and a
`risk:low`/`risk:high` label the Triager applies to every `state:triage`
issue (see below) — `risk:low` issues are auto-promoted to `state:ready`;
`risk:high` ones are left at `state:triage` for a human to promote.

## Files
- `prompts/*.md` — the actual instructions given to each agent. Only the
  Tuner is allowed to propose edits to these, and only via PR.
- `scripts/lib.py` — shared helpers: config loading, state/log I/O, the
  gh-backed issue state machine, and a non-shell subprocess wrapper (every
  git/gh/claude call uses argument lists, never a shell string).
- `scripts/gate.py` — deterministic pre-checks (no LLM). Must pass before
  the Verifier agent is even invoked. Importable (`check_gate()`) or
  runnable standalone.
- `scripts/scan.py`, `triage.py`, `build.py`, `verify.py`, `tune.py` —
  one `claude -p` call each, scoped to a single job.
- `scripts/triage.py` — classifies each `state:triage` issue's risk
  (`risk:low`/`risk:high`) and auto-promotes `risk:low` ones to
  `state:ready`. `risk:high` issues (anything touching agent tool
  permissions, `FORBIDDEN_PATH_REGEX`, or the gate/overseer safety
  checks themselves) are left for a human to review and promote.
- `scripts/overseer.py` — reads `.harness/log.jsonl`, enforces global
  thresholds (including the human-reopen check), can write
  `.harness/halt.lock` to pause everything.
- `scripts/run.py` — the daemon loop tying it all together. Re-reads
  `config.env` at the start of every loop iteration, so an edit (e.g. a
  merged Tuner PR) takes effect on the next tick without a daemon restart.
- `.harness/log.jsonl` — append-only event log every script writes to.
- `.harness/state.json` — running counters (daily cost, backlog size, etc).

## Exit conditions (see config.env)
- `MAX_RETRIES` per issue before `state:needs-human`
- `MAX_OPEN_AUTO_ISSUES` — Scanner stops filing once backlog this large
- `MAX_REJECT_RATE` over trailing N issues — Overseer halts daemon
- `MAX_DAILY_CYCLES` / `MAX_DAILY_CLAUDE_CALLS` — hard daily ceiling
- oscillation check in gate.sh — same file touched by 3+ consecutive
  attempts across an issue's history with no net diff progress
