"""Shared helpers imported by every harness script.

Design notes vs. the old bash version:
- All subprocess calls use argument lists (never shell=True), so there is
  no shell quoting/delimiter layer to get wrong.
- All structured data (state, log lines, package.json) goes through the
  json module instead of jq/bc, so there is no separate-process JSON
  parsing to keep in sync with shell escaping.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# Resolved to an absolute path *now*, at import time -- before any later
# os.chdir() (verify.py changes cwd into an isolated worktree mid-run).
# A relative Path here would silently re-resolve against whatever the
# process's cwd happens to be at each file access.
HARNESS_DIR = Path(os.environ.get("HARNESS_DIR", ".harness")).resolve()
LOG_FILE = HARNESS_DIR / "log.jsonl"
STATE_FILE = HARNESS_DIR / "state.json"
HALT_FILE = HARNESS_DIR / "halt.lock"

HARNESS_DIR.mkdir(parents=True, exist_ok=True)
if not STATE_FILE.exists():
    STATE_FILE.write_text(json.dumps({"daily_cycles": 0, "daily_claude_calls": 0, "date": ""}))


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run wrapper: list-based args, text mode, never raises by
    default. Resolves cmd[0] via shutil.which() first -- with shell=False,
    Windows won't find e.g. npm (actually npm.cmd) by bare name the way a
    POSIX exec/PATH search would. And a resolved .cmd/.bat still can't be
    launched directly by CreateProcess -- it needs cmd.exe /c as the actual
    launched program, which keeps this an argv list, not a shell string."""
    kwargs.setdefault("text", True)
    kwargs.setdefault("capture_output", True)
    # Force UTF-8 explicitly: text=True alone uses the platform locale
    # encoding (cp1252 on Windows), which chokes on non-ASCII bytes gh/git
    # output routinely contains (e.g. an em dash in an issue comment).
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    resolved = shutil.which(cmd[0])
    if resolved:
        cmd = [resolved, *cmd[1:]]
        if resolved.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", *cmd]
    return subprocess.run(cmd, **kwargs)


# --- event log / state ---

def log_event(event: str, issue: str, extra: dict | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "issue": str(issue),
    }
    entry.update(extra or {})
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def is_halted() -> bool:
    return HALT_FILE.exists()


def halt_daemon(reason: str) -> None:
    HALT_FILE.write_text(reason, encoding="utf-8")
    log_event("halt", "-", {"reason": reason})


def _read_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _write_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def reset_daily_counters_if_new_day() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state = _read_state()
    if state.get("date") != today:
        state["date"] = today
        state["daily_cycles"] = 0
        state["daily_claude_calls"] = 0
        _write_state(state)


def bump_counter(key: str) -> None:
    state = _read_state()
    state[key] = state.get(key, 0) + 1
    _write_state(state)


def get_counter(key: str) -> int:
    return _read_state().get(key, 0)


# --- GitHub issue state machine ---

_RETRY_RE = re.compile(r"^retry:(\d+)$")


def get_retry_count(issue) -> int:
    result = run(["gh", "issue", "view", str(issue), "--json", "labels", "-q", ".labels[].name"])
    for line in result.stdout.splitlines():
        m = _RETRY_RE.match(line.strip())
        if m:
            return int(m.group(1))
    return 0


def set_retry_count(issue, n: int) -> None:
    old = get_retry_count(issue)
    run(["gh", "issue", "edit", str(issue), "--remove-label", f"retry:{old}"])
    run(["gh", "issue", "edit", str(issue), "--add-label", f"retry:{n}"])


_STATES = ["triage", "ready", "in-progress", "in-review", "needs-human"]


def set_state_label(issue, new_state: str) -> None:
    for s in _STATES:
        run(["gh", "issue", "edit", str(issue), "--remove-label", f"state:{s}"])
    run(["gh", "issue", "edit", str(issue), "--add-label", f"state:{new_state}"])


# --- config.env loader ---

def load_config(path) -> dict[str, str]:
    """Parses the existing KEY=VALUE config.env format (kept as-is so the
    file stays human-editable and compatible with the bash-era docs)."""
    config: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def cfg_int(config: dict, key: str) -> int:
    return int(config[key])


def cfg_bool(config: dict, key: str) -> bool:
    return config[key].strip().lower() == "true"
