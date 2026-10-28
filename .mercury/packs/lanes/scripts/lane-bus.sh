#!/usr/bin/env bash
# .mercury/packs/lanes/scripts/lane-bus.sh — run lane-bus.py (Issue #712) with a Python that really runs: a Windows
# "python3" can be the Store placeholder that exits without doing anything. Arguments pass through unchanged.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$cand" -B "$HERE/lane-bus.py" "$@"
  fi
done
echo "lane-bus: Python 3.11 or later is required" >&2
exit 2
