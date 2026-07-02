#!/usr/bin/env bash
# sot-codex-serve.sh — start the SoT Codex read-only API locally for talent-validate.
#
# Reads the SoT Codex app + seed READ-ONLY and isolates the SQLite DB to Mercury's tmp, so it
# NEVER writes the SoT repo (CLAUDE.md hard constraint). SoT-workflow tool from roadmap §1.3/§9
# — Mercury hosts it; the SoT repos are untouched.
#
# Probed working stack (2026-06-22): uvicorn 0.39.0 / CPython 3.14.3.
#
# Usage (run in its own terminal; it stays in the foreground serving):
#   SOT_CODEX_DIR=/path/to/SoT-fyc-space bash scripts/sot-codex-serve.sh
#   # then, in another terminal / Claude session:
#   /talent-validate { "talent_id": "ss_jianqie", "class_id": "ss" }
#
# Env:
#   SOT_CODEX_DIR  SoT Codex repo (REQUIRED — no default: repo locations are machine-specific,
#                  and a silently wrong default is worse than a loud error)
#   CODEX_PORT     port (default 8000)
#   CODEX_DB       SQLite path — kept under Mercury tmp by default (never the SoT repo)
set -uo pipefail

SOT_CODEX="${SOT_CODEX_DIR:-}"
if [[ -z "$SOT_CODEX" ]]; then
  echo "ERROR: SOT_CODEX_DIR is not set. Point it at your SoT Codex repo checkout, e.g.:" >&2
  echo "  SOT_CODEX_DIR=/path/to/SoT-fyc-space bash scripts/sot-codex-serve.sh" >&2
  exit 2
fi
PORT="${CODEX_PORT:-8000}"
# Repo root derived from this script's location so the default DB never hardcodes a machine path.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${CODEX_DB:-$REPO_ROOT/.mercury/tmp/codex-readonly.db}"

PY="$SOT_CODEX/.venv/Scripts/python.exe"
[[ -f "$PY" ]] || PY="$SOT_CODEX/.venv/bin/python"
if [[ ! -f "$PY" ]]; then
  echo "ERROR: SoT Codex venv python not found (looked for $SOT_CODEX/.venv/Scripts/python.exe and .../bin/python)" >&2
  echo "       Set SOT_CODEX_DIR, or build the venv:" >&2
  echo "       cd \"$SOT_CODEX\" && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt" >&2
  exit 2
fi
if [[ ! -f "$SOT_CODEX/seed/seed.json" ]]; then
  echo "ERROR: seed.json not found under $SOT_CODEX/seed/" >&2
  exit 2
fi

# Reuse a live instance instead of starting a confusing second one on the same port — but only
# when the responder actually looks like the SoT Codex API (/api/tags returns a JSON array;
# shape probed against the live service). curl -f: an HTTP error (404/500 from an unrelated
# service) must not read as "already serving".
HEALTH="$(curl -fsS -m 2 "http://127.0.0.1:$PORT/api/tags" 2>/dev/null || true)"
if [[ -n "$HEALTH" ]]; then
  if printf '%s' "$HEALTH" | grep -qE '^\s*\['; then
    echo "NOTE: port $PORT is already serving SoT Codex (/api/tags returned tag JSON) — reusing it."
    echo "      Stop the existing instance first if it is stale."
    exit 0
  fi
  echo "ERROR: port $PORT is occupied by a non-Codex service; refusing to reuse it." >&2
  exit 2
fi

mkdir -p "$(dirname "$DB")"
echo "Starting SoT Codex read-only API on http://127.0.0.1:$PORT"
echo "  DB -> $DB  (SoT repo untouched)"
echo "  Ctrl-C to stop. Leave it running while you use /talent-validate."
exec env PYTHONPATH="$SOT_CODEX" DB_PATH="$DB" SEED_PATH="$SOT_CODEX/seed/seed.json" \
  "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --app-dir "$SOT_CODEX"
