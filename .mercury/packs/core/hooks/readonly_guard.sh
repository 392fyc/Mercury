#!/bin/sh
# Launcher of the read-only agent guard (see readonly_guard.py). Run as: sh readonly_guard.sh  (no executable bit needed).
# It finds a working Python 3 and runs the guard with the hook payload on stdin. If no interpreter works it exits 2,
# which blocks the tool call: a read-only agent must not run unguarded commands. The probe runs the interpreter, so
# the Windows Store stub named python3 (which is not a Python) is skipped.
here=$(dirname "$0")
for py in python3 python py; do
  if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
    exec "$py" -B "$here/readonly_guard.py"
  fi
done
echo "Mercury read-only guard: no working Python 3.8+ found (tried python3, python, py); Bash is blocked for this read-only agent." >&2
exit 2
