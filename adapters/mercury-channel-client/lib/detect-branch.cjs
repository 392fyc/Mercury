'use strict';

// Branch detection for mercury-channel-client.
// Issue #297: top-level execSync of `git branch --show-current` blocked module
// load for 50-200ms (longer if git was slow/hung). This module replaces that
// with: (a) synchronous env-var read (zero I/O) for the initial value, and
// (b) async execFile lookup deferred until after mcp.connect() so the MCP
// server initialization is not blocked by git.

const { execFile } = require('child_process');

// Initial value at module load — no I/O. Set MERCURY_BRANCH_OVERRIDE to skip
// async git lookup entirely (useful in non-git contexts, CI, or when the
// caller already knows the branch).
function detectBranchSync() {
  return process.env.MERCURY_BRANCH_OVERRIDE || 'unknown';
}

// Async git branch lookup. Returns the override if set; otherwise execs
// `git branch --show-current` with a 2s timeout and falls back to 'unknown'
// on any error (non-git cwd, git missing, timeout, etc.).
//
// `cwd` is pinned to CLAUDE_PROJECT_DIR (set by Claude Code) when available,
// falling back to the process cwd. This guards against callers chdir'ing
// before the deferred branch lookup fires — without the pin, we'd silently
// read the wrong repo's branch (or get 'unknown' in a non-git tmpdir).
function detectBranchAsync({
  timeoutMs = 2000,
  cwd = process.env.CLAUDE_PROJECT_DIR || process.cwd(),
} = {}) {
  return new Promise(resolve => {
    if (process.env.MERCURY_BRANCH_OVERRIDE) {
      resolve(process.env.MERCURY_BRANCH_OVERRIDE);
      return;
    }
    execFile(
      'git',
      ['branch', '--show-current'],
      { encoding: 'utf8', timeout: timeoutMs, windowsHide: true, cwd },
      (err, stdout) => {
        if (err) { resolve('unknown'); return; }
        const v = String(stdout || '').trim();
        resolve(v || 'unknown');
      }
    );
  });
}

module.exports = { detectBranchSync, detectBranchAsync };
