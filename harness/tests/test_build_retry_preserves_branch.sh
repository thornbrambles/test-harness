#!/usr/bin/env bash
# Regression test for issue #1: build.sh must not wipe a retry branch back
# to main on every attempt. On retry N>0 the branch's prior commits must be
# preserved so gate.sh's cross-attempt oscillation check can ever see 3+
# accumulated commits.
#
# This test stubs out `gh` and `claude` (no network/auth needed) and drives
# the real scripts/build.sh against a throwaway local git repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SH="$SCRIPT_DIR/../scripts/build.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAKE_BIN="$WORK/fakebin"
REPO="$WORK/repo"
mkdir -p "$FAKE_BIN" "$REPO"

# --- fake `gh`: answers just enough of the CLI surface build.sh needs ---
cat > "$FAKE_BIN/gh" <<'EOF'
#!/usr/bin/env bash
args="$*"
case "$args" in
  *"issue view"*"--json body"*)
    echo "${FAKE_ISSUE_BODY:-stub issue body}"
    ;;
  *"issue view"*"--json comments"*)
    echo ""
    ;;
  *"issue view"*"--json labels"*)
    echo "retry:${FAKE_RETRY_COUNT:-0}"
    ;;
  *)
    exit 0
    ;;
esac
EOF
chmod +x "$FAKE_BIN/gh"

# --- fake `claude`: simulates the Builder agent making exactly one commit ---
cat > "$FAKE_BIN/claude" <<'EOF'
#!/usr/bin/env bash
n="$(cat .attempt_counter 2>/dev/null || echo 0)"
n=$((n + 1))
echo "$n" > .attempt_counter
echo "attempt-$n" >> builder_output.txt
git add builder_output.txt .attempt_counter
git commit -m "stub builder commit $n" >/dev/null
EOF
chmod +x "$FAKE_BIN/claude"

export PATH="$FAKE_BIN:$PATH"
export HARNESS_DIR=".harness"

cd "$REPO"
git init -q -b main
git config user.email "test@example.com"
git config user.name "Test"
echo "hello" > README.md
git add README.md
git commit -q -m "init"

pass=1

echo "=== attempt 1 (retry:0) ==="
FAKE_RETRY_COUNT=0 bash "$BUILD_SH" 1 >"$WORK/out1.txt" 2>&1
COMMITS_AFTER_1="$(git log --oneline main..auto/issue-1 | wc -l | tr -d ' ')"
if [ "$COMMITS_AFTER_1" -ne 1 ]; then
  echo "FAIL: expected 1 commit on branch after first attempt, got $COMMITS_AFTER_1"
  cat "$WORK/out1.txt"
  pass=0
fi

echo "=== attempt 2 (retry:1, simulating a Verifier rejection) ==="
FAKE_RETRY_COUNT=1 bash "$BUILD_SH" 1 >"$WORK/out2.txt" 2>&1
COMMITS_AFTER_2="$(git log --oneline main..auto/issue-1 | wc -l | tr -d ' ')"
if [ "$COMMITS_AFTER_2" -ne 2 ]; then
  echo "FAIL: expected prior attempt's commit to be preserved (2 commits total), got $COMMITS_AFTER_2"
  echo "  (this is exactly the bug in #1: checkout -B resets the branch to main on every retry,"
  echo "   so PRIOR_COMMITS can never reach 3 and gate.sh's oscillation check can never fire)"
  cat "$WORK/out2.txt"
  pass=0
fi

CONTENT="$(git show auto/issue-1:builder_output.txt 2>/dev/null || true)"
if ! printf '%s\n' "$CONTENT" | grep -q "attempt-1" || ! printf '%s\n' "$CONTENT" | grep -q "attempt-2"; then
  echo "FAIL: branch content lost the first attempt's change; got: $CONTENT"
  pass=0
fi

if [ "$pass" -eq 1 ]; then
  echo "PASS: retries accumulate commits on the existing branch instead of resetting to main"
  exit 0
else
  exit 1
fi
