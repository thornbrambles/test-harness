#!/usr/bin/env bash
# Runs every tests/test_*.sh script and fails if any of them fail.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

status=0
for t in "$DIR"/test_*.sh; do
  echo "--- running $(basename "$t") ---"
  if ! bash "$t"; then
    status=1
  fi
done

exit "$status"
