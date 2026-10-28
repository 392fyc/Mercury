#!/bin/sh
# Launcher of the wait guard (see wait_guard.py). Run as: sh wait_guard.sh  (no executable bit needed).
# It finds a working Python 3 and runs the guard with the hook payload on stdin. Unlike the read-only guard it fails
# open: without an interpreter it allows the call and says so, because a missing Python must not stop all shell work of
# a writing role. The rule in the task contract still applies.
here=$(dirname "$0")
for py in python3 python py; do
  if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >/dev/null 2>&1; then
    exec "$py" -B "$here/wait_guard.py"
  fi
done
echo "Mercury wait guard: no working Python 3.8+ found (tried python3, python, py); the command was not checked." >&2
exit 0
