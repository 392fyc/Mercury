#!/usr/bin/env bash
# scripts/toolcall-xml-lint.sh — deterministic guard against literal tool-call XML markers
#   leaking into Claude-context files (Issue #527).
#
# WHY: Anthropic's Opus-4.8 generation-side defect (anthropics/claude-code #62344 / #66153 /
#   #70241 et al. — all OPEN, no fixed-in tag) is amplified by a SELF-POISONING loop: when a
#   context-injected file (CLAUDE.md, an agent/skill doc, a memory file, a handoff) contains a
#   LITERAL tool-call marker, the model copies it as a template and the malformed call leaks as
#   plain text, hanging the turn with no output. This linter keeps the count of such literal
#   markers at ZERO across the files Claude auto-loads, so the repo never seeds that loop.
#   (Mitigation route recorded in Issue #527's research comment; keeps the count-at-zero
#   invariant per anthropics/claude-code #70241.)
#
# SELF-POISONING SAFETY: every marker keyword in MARKER_RE below is BROKEN with a single-char
#   regex class (e.g. f[u]nction). grep -E expands each class back to the intact marker when it
#   scans a TARGET file, but THIS script's own source holds no intact marker — so the linter
#   never matches itself and never pollutes context if it is ever read into a prompt. Reported
#   lines have their angle brackets masked (‹ ›), so the report output is inert too.
#
# Usage:
#   ./scripts/toolcall-xml-lint.sh [path ...]   # default: Claude-context markdown files in repo
#   TOOLCALL_LINT_VERBOSE=1 ./scripts/toolcall-xml-lint.sh   # also print each masked offending line
# Exit: 0 = clean; 1 = literal marker(s) found; 2 = usage/environment error.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || { echo "ERROR: cannot resolve repo root" >&2; exit 2; }
cd "$REPO_ROOT" || { echo "ERROR: cannot cd to repo root '$REPO_ROOT'" >&2; exit 2; }

# Broken-keyword alternation (see SELF-POISONING SAFETY). Each [x] is a one-char class that
# grep -E expands to x when matching target files; this literal source line stays inert.
MARKER_RE='<f[u]nction_calls>|</f[u]nction_calls>|<in[v]oke[[:space:]]+name=|</in[v]oke>|<par[a]meter[[:space:]]+name=|</par[a]meter>|an[t]ml:in[v]oke|an[t]ml:f[u]nction_calls|an[t]ml:par[a]meter'

# Build the scan set. Explicit args (files or dirs) override the default Claude-context set.
declare -a FILES
if [[ "$#" -gt 0 ]]; then
  while IFS= read -r line; do [[ -n "$line" ]] && FILES+=("$line"); done < <(
    for p in "$@"; do
      if [[ -d "$p" ]]; then find "$p" -type f -name '*.md' 2>/dev/null
      elif [[ -f "$p" ]]; then printf '%s\n' "$p"
      else echo "WARN: skip non-existent path: $p" >&2
      fi
    done
  )
else
  # Default: the markdown/text files Claude auto-loads at session start or a skill injects.
  while IFS= read -r line; do [[ -n "$line" ]] && FILES+=("$line"); done < <(
    for f in CLAUDE.md CLAUDE.local.md AGENTS.md; do [[ -f "$f" ]] && printf '%s\n' "$f"; done
    find .claude .mercury/docs -type f -name '*.md' 2>/dev/null
  )
fi

# Fail closed on an empty scan set: a typoed path arg, or a repo somehow missing its
# Claude-context files, must NOT read as a silent PASS (a skipped gate that still passes is
# worse than a loud error). Exit 2 = usage/environment error.
# NB: use the ${arr[*]+set} form, not ${#FILES[@]} — an EMPTY array under `set -u` trips
# "unbound variable" on bash < 4.4 (e.g. some Git Bash builds); the +set form is portable.
if [[ -z "${FILES[*]+set}" ]]; then
  echo "ERROR: no files to scan — bad path arg(s), or repo missing Claude-context files. Failing closed." >&2
  exit 2
fi

# NOTE: this linter and its test are deliberately NOT excluded from scanning. They live under
# the same broken-keyword discipline as every guarded file — the linter's own source and the
# test's fixtures/labels must be marker-free, and the test asserts exactly that. Excluding them
# would open a blind spot where an intact marker could hide in a "skipped" file.
hits=0
scanned=0
for f in "${FILES[@]}"; do
  scanned=$((scanned+1))
  # grep -I skips binary; -n gives line numbers. Read numbers off, then re-extract the line
  # for masked display so no intact marker is ever echoed.
  while IFS=: read -r lineno _rest; do
    [[ -z "$lineno" ]] && continue
    hits=$((hits+1))
    echo "toolcall-xml-lint: literal tool-call marker at $f:$lineno"
    if [[ "${TOOLCALL_LINT_VERBOSE:-0}" == "1" ]]; then
      sed -n "${lineno}p" "$f" | sed 's/</‹/g; s/>/›/g'
    fi
  done < <(grep -E -n -I "$MARKER_RE" "$f" 2>/dev/null)
done

echo "--------------------------------------"
if [[ "$hits" -gt 0 ]]; then
  echo "toolcall-xml-lint: FAIL — $hits literal tool-call marker(s) across $scanned file(s)."
  echo "  Escape them so the model cannot copy them as a template: use HTML entities (e.g. &lt; / &gt;)"
  echo "  or split the keyword with a zero-width break. Background + rationale: Issue #527."
  exit 1
fi
echo "toolcall-xml-lint: PASS — no literal tool-call markers in $scanned Claude-context file(s)."
exit 0
