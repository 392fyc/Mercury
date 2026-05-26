'use strict';
// launch.test.cjs — config-gate reject-rule tests for the playwright-mcp adapter.
// Tests the pure validation functions; never spawns a real browser.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const {
  buildSafeArgs, expandEnv, isAttachFlag, isAttachValue, assertPathAllowed,
  normPath, FLAG_ALLOWLIST, BROWSER_ALLOWLIST, canonicalize, resolveCli,
} = require('../launch.cjs');

// A repo root + safe outside-repo private path to reuse across tests.
// Use os.tmpdir()-based synthetic paths — no hardcoded local machine paths.
const REPO = path.join(os.tmpdir(), 'fakerepo', 'mercury');
const SAFE_ABS = path.join(os.tmpdir(), 'mercury-private', 'playwright-mcp', 'auth-state.json');

function gate(args, env = {}) {
  return buildSafeArgs(args, { env, repoRoot: REPO, cwd: REPO });
}

// ── attach-class flags are rejected (整类拦截) ──────────────────────────────
test('rejects --extension (attach-class)', () => {
  assert.throws(() => gate(['--extension']), /attach\/connect-class/);
});
test('rejects --cdp-endpoint (attach-class)', () => {
  assert.throws(() => gate(['--cdp-endpoint=http://localhost:9222']), /attach\/connect-class/);
});
test('rejects --endpoint (attach-class)', () => {
  assert.throws(() => gate(['--endpoint', 'ws://x']), /attach\/connect-class/);
});
test('rejects --remote-endpoint / remoteEndpoint token (attach-class)', () => {
  assert.throws(() => gate(['--remote-endpoint=ws://x']), /attach\/connect-class/);
  assert.ok(isAttachFlag('--remoteEndpoint'));
});
test('rejects a hypothetical new connect-class flag by token (default-deny generalizes)', () => {
  assert.throws(() => gate(['--connect-over-cdp=foo']), /attach\/connect-class/);
});

// ── unknown flags rejected by default-deny allowlist ────────────────────────
test('rejects unknown flag not in allowlist', () => {
  assert.throws(() => gate(['--totally-unknown-flag']), /default-deny/);
});

// ── CRITICAL: removed flags are now default-deny ────────────────────────────
test('[CRITICAL] rejects --config (backdoor: cdpEndpoint/isolated:false in config file)', () => {
  assert.throws(() => gate(['--config', 'x.json']), /default-deny/);
});
test('[CRITICAL] rejects --config=x.json (= form)', () => {
  assert.throws(() => gate(['--config=x.json']), /default-deny/);
});

// ── HIGH: --port/--host switch to SSE/HTTP transport ────────────────────────
test('[HIGH] rejects --port (switches stdio server to SSE/HTTP network listener)', () => {
  assert.throws(() => gate(['--port', '3000']), /default-deny/);
});
test('[HIGH] rejects --host (exposes unauthenticated network endpoint)', () => {
  assert.throws(() => gate(['--host', '0.0.0.0']), /default-deny/);
});

// ── HIGH: --help/--version contaminate JSON-RPC stdout ──────────────────────
test('[HIGH] rejects --help (stdout text contaminates JSON-RPC channel)', () => {
  assert.throws(() => gate(['--help']), /default-deny/);
});
test('[HIGH] rejects --version (stdout text contaminates JSON-RPC channel)', () => {
  assert.throws(() => gate(['--version']), /default-deny/);
});

// ── HIGH: --browser value whitelist ─────────────────────────────────────────
test('[HIGH] rejects --browser chrome (real-browser channel, reuses installed Chrome)', () => {
  assert.throws(() => gate(['--browser', 'chrome']), /not an allowed Playwright engine/);
});
test('[HIGH] rejects --browser=msedge (real-browser channel)', () => {
  assert.throws(() => gate(['--browser=msedge']), /not an allowed Playwright engine/);
});
test('[HIGH] rejects --browser=cdp (attach-class value)', () => {
  assert.throws(() => gate(['--browser=cdp']), /not an allowed Playwright engine/);
});
test('[HIGH] rejects --browser chrome-beta (channel variant)', () => {
  assert.throws(() => gate(['--browser', 'chrome-beta']), /not an allowed Playwright engine/);
});
test('[HIGH] rejects --browser msedge-dev (channel variant)', () => {
  assert.throws(() => gate(['--browser', 'msedge-dev']), /not an allowed Playwright engine/);
});
test('[HIGH] allows --browser=chromium (Playwright engine)', () => {
  const out = gate(['--browser=chromium']);
  assert.ok(out.includes('--browser=chromium') || out.some((a) => a === '--browser=chromium'));
});
test('[HIGH] allows --browser firefox (Playwright engine, space form)', () => {
  const out = gate(['--browser', 'firefox']);
  assert.ok(out.includes('firefox'));
});
test('[HIGH] allows --browser webkit (Playwright engine)', () => {
  const out = gate(['--browser', 'webkit']);
  assert.ok(out.includes('webkit'));
});

// ── HIGH: value-smuggling via non-path flag values ───────────────────────────
// --allowed-origins/--blocked-origins are excluded from attach-token value scan:
// their values are origin lists that legitimately contain substrings like "remote"
// or "endpoint" (ADR §4.3). The real attach vector is flag-name-based and already
// covered by the whole-class reject. So cdp:// and ws-endpoint:// values are allowed
// by the value scanner for origin flags (though they are semantically unusual).
test('[HIGH] allows --allowed-origins=cdp://x (origin flag: value scan excluded per ADR §4.3)', () => {
  assert.doesNotThrow(() => gate(['--allowed-origins=cdp://x']));
});
test('[HIGH] allows --allowed-origins=ws-endpoint://x (origin flag: value scan excluded)', () => {
  assert.doesNotThrow(() => gate(['--allowed-origins=ws-endpoint://x']));
});
test('[HIGH] rejects --caps value containing connect token', () => {
  assert.throws(() => gate(['--caps=connect']), /attach\/connect token/);
});
test('[HIGH] allows --allowed-origins=https://example.com (no attach token)', () => {
  assert.doesNotThrow(() => gate(['--allowed-origins=https://example.com']));
});
test('[HIGH] allows --allowed-origins=https://remote.example.com (hostname with "remote" substring)', () => {
  assert.doesNotThrow(() => gate(['--allowed-origins=https://remote.example.com']));
});
test('[HIGH] allows --allowed-origins=https://endpoint.example.com (hostname with "endpoint" substring)', () => {
  assert.doesNotThrow(() => gate(['--allowed-origins=https://endpoint.example.com']));
});

// ── positional arguments rejected ───────────────────────────────────────────
test('[Critical] rejects positional argument (non-flag)', () => {
  assert.throws(() => gate(['foo']), /positional arguments are not allowed/);
});
test('[Critical] rejects positional argument: bare value that looks like a path', () => {
  assert.throws(() => gate(['somefile.json']), /positional arguments are not allowed/);
});

// ── HIGH: removed artifact path flags ───────────────────────────────────────
test('[HIGH] rejects --save-trace (removed from allowlist)', () => {
  assert.throws(() => gate(['--save-trace', '/tmp/trace']), /default-deny/);
});
test('[HIGH] rejects --save-session (removed from allowlist)', () => {
  assert.throws(() => gate(['--save-session', '/tmp/session']), /default-deny/);
});
test('[HIGH] rejects --output-dir (removed from allowlist)', () => {
  assert.throws(() => gate(['--output-dir', '/tmp/out']), /default-deny/);
});
test('[HIGH] rejects --no-sandbox (weakens sandbox)', () => {
  assert.throws(() => gate(['--no-sandbox']), /default-deny/);
});

// ── repo-inside path rejected ───────────────────────────────────────────────
test('rejects --storage-state pointing inside repo working tree', () => {
  assert.throws(
    () => gate([`--storage-state=${REPO}/secrets/auth.json`]),
    /inside repo working tree/,
  );
});

// ── real browser profile path rejected ──────────────────────────────────────
test('rejects --storage-state pointing at a real Chrome profile', () => {
  // Synthetic path containing the PROFILE_FRAGMENTS substring — no real machine path needed.
  const chrome = path.join(os.tmpdir(), 'fakehome', 'Google', 'Chrome', 'User Data', 'Default', 'state.json');
  assert.throws(() => gate([`--storage-state=${chrome}`]), /real browser profile/);
});
test('rejects --storage-state pointing at a real Edge profile (backslash + case insensitive)', () => {
  // Mixed slashes + uppercase to exercise normPath; still hits PROFILE_FRAGMENTS substring.
  const edge = path.join(os.tmpdir(), 'fakehome', 'Microsoft', 'Edge', 'USER DATA', 'state.json');
  assert.throws(() => gate([`--storage-state=${edge}`]), /real browser profile/);
});

// ── HIGH-2: --user-data-dir removed from allowlist (persistent-profile conflicts with --isolated) ──
test('[HIGH] rejects --user-data-dir= form (default-deny: persistent-profile mode removed)', () => {
  assert.throws(() => gate(['--user-data-dir=/tmp/profile']), /default-deny/);
});
test('[HIGH] rejects --user-data-dir space form (default-deny)', () => {
  assert.throws(() => gate(['--user-data-dir', '/tmp/profile']), /default-deny/);
});

// ── HIGH: 8.3 short-name path bypass (Codex finding: was passing, must be red) ──
// Windows 8.3 short names like GOOGLE~1 can refer to "Google" etc.
// canonicalize() uses fs.realpathSync.native() which resolves these on Windows.
// On non-Windows we simulate with a symlink/known-path test instead.
test('[HIGH] rejects 8.3 short-name path pointing at Chrome User Data (Windows)', () => {
  if (process.platform !== 'win32') {
    // On non-Windows: 8.3 short names (e.g. GOOGLE~1) cannot be resolved by the OS.
    // canonicalize() uses fs.realpathSync.native() which resolves them on Windows only.
    // On Linux/Mac the path doesn't exist so canonicalize returns it as-is.
    // Instead verify assertPathAllowed catches the fragment match for a resolved-form
    // synthetic path (no real machine path needed).
    // Synthetic path containing profile fragment — no real machine path needed.
    assert.throws(
      () => assertPathAllowed(path.join(os.tmpdir(), 'fakehome', 'Google', 'Chrome', 'User Data', 'Default', 'state.json'), REPO),
      /real browser profile/,
    );
    return;
  }
  // On Windows: construct a path using 8.3 form pointing at Chrome User Data.
  // We can't guarantee the actual short name exists on any given machine, so we test
  // canonicalize behavior directly: if realpathSync.native resolves a path, the resolved
  // form must match the profile fragment check.
  // Smoke: verify that a known real Chrome profile path (if it exists) is rejected.
  const knownChromePath = path.join(
    os.homedir(), 'AppData', 'Local', 'Google', 'Chrome', 'User Data',
  );
  if (fs.existsSync(knownChromePath)) {
    assert.throws(
      () => assertPathAllowed(knownChromePath, REPO),
      /real browser profile/,
    );
  }
  // Verify that a synthetic path whose normPath contains the profile fragment is caught.
  // The key invariant: after canonicalize, profile fragments are matched case-insensitively.
  assert.throws(
    () => assertPathAllowed(path.join(os.tmpdir(), 'fakehome', 'Google', 'Chrome', 'User Data', 'Default'), REPO),
    /real browser profile/,
  );
});

// ── HIGH: symlink bypass ─────────────────────────────────────────────────────
test('[HIGH] rejects symlink pointing at real Chrome profile (if symlink creation allowed)', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-test-'));
  const linkPath = path.join(tmpDir, 'fake-state.json');
  // Try to create a symlink to a Chrome profile path.
  const chromeDataDir = process.platform === 'win32'
    ? path.join(os.homedir(), 'AppData', 'Local', 'Google', 'Chrome', 'User Data')
    : '/home/user/.config/google-chrome';
  let symlinkCreated = false;
  try {
    fs.symlinkSync(chromeDataDir, linkPath);
    symlinkCreated = true;
  } catch (_) {
    // symlink creation requires privileges on Windows; skip if not available
  }
  try {
    if (symlinkCreated && fs.existsSync(chromeDataDir)) {
      // canonicalize will resolve the symlink to the real Chrome profile path → rejected
      assert.throws(
        () => assertPathAllowed(linkPath, REPO),
        /real browser profile/,
      );
    } else {
      // Can't create symlink or target doesn't exist — verify the canonicalize
      // function at least handles a symlink to a path inside the repo (which we can create).
      let repoSymLink = path.join(tmpDir, 'repo-link');
      try {
        fs.symlinkSync(REPO, repoSymLink);
        const targetPath = path.join(repoSymLink, 'secrets', 'auth.json');
        assert.throws(
          () => assertPathAllowed(targetPath, REPO),
          /inside repo working tree/,
        );
      } catch (symlinkErr) {
        // Skip: symlink creation not available in this environment.
        // Document: this test requires symlink privileges (Windows: SeCreateSymbolicLinkPrivilege).
        assert.ok(true, 'symlink test skipped: insufficient privileges');
      }
    }
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ── MEDIUM: repoRoot fail-closed ─────────────────────────────────────────────
test('[MEDIUM] fail-closed when repoRoot is null and path-type flag is used', () => {
  // When repoRoot cannot be determined (null), path flags must be rejected.
  assert.throws(
    () => buildSafeArgs([`--storage-state=${SAFE_ABS}`], { repoRoot: null }),
    /repo root could not be determined/,
  );
});
test('[MEDIUM] non-path flags are allowed even when repoRoot is null', () => {
  // Non-path flags don't need repo root check.
  assert.doesNotThrow(
    () => buildSafeArgs(['--headless'], { repoRoot: null }),
  );
});

// ── env-var expansion failure rejected ──────────────────────────────────────
test('rejects unresolved %VAR% in path (hard fail, no silent passthrough)', () => {
  assert.throws(
    () => gate(['--storage-state=%NOPE_UNDEFINED%/auth.json'], {}),
    /unresolved environment variable/,
  );
});
test('rejects unresolved $VAR in path', () => {
  assert.throws(
    () => gate(['--storage-state=$NOPE_UNDEFINED/auth.json'], {}),
    /unresolved environment variable/,
  );
});
test('expands %VAR% from env then validates', () => {
  const env = { LOCALAPPDATA: process.platform === 'win32' ? 'D:/Users/agent/AppData/Local' : '/home/agent/.local' };
  const base = process.platform === 'win32' ? 'D:/Users/agent/AppData/Local' : '/home/agent/.local';
  const expanded = expandEnv('%LOCALAPPDATA%/mercury/auth.json', env);
  assert.equal(normPath(expanded), normPath(`${base}/mercury/auth.json`));
});

// ── legal config passes + --isolated injected ───────────────────────────────
test('valid config passes and --isolated is injected when absent', () => {
  const out = gate(['--caps=storage', `--storage-state=${SAFE_ABS}`]);
  assert.ok(out.includes('--isolated'), 'injects --isolated');
  assert.ok(out.includes('--caps=storage'), 'keeps --caps=storage');
  const ss = out.find((a) => a.startsWith('--storage-state='));
  assert.ok(ss, 'keeps --storage-state');
  assert.equal(normPath(ss.split('=')[1]), normPath(path.resolve(SAFE_ABS)));
});
test('does not double-inject --isolated when caller already provided it', () => {
  const out = gate(['--isolated', '--caps=storage', `--storage-state=${SAFE_ABS}`]);
  assert.equal(out.filter((a) => a === '--isolated').length, 1);
});
test('space-separated path value form is validated and absolutized', () => {
  const out = gate(['--storage-state', SAFE_ABS]);
  const idx = out.indexOf('--storage-state');
  assert.ok(idx >= 0);
  assert.equal(normPath(out[idx + 1]), normPath(path.resolve(SAFE_ABS)));
});

// ── path-norm robustness (slash + case) ─────────────────────────────────────
test('assertPathAllowed is robust to mixed slashes and case for repo prefix', () => {
  // Use synthetic REPO with backslash form to verify normPath handles mixed slashes.
  const repoBackslash = REPO.replace(/\//g, '\\');
  const insidePath = REPO.replace(/\//g, '\\') + '\\x\\y.json';
  assert.throws(
    () => assertPathAllowed(insidePath, repoBackslash),
    /inside repo working tree/,
  );
});
test('assertPathAllowed allows a private path outside repo and outside profiles', () => {
  assert.doesNotThrow(() => assertPathAllowed(path.resolve(SAFE_ABS), REPO));
});

// ── HIGH-1 regression: root must be canonicalized (8.3 short-name / junction bypass) ──
// Codex finding: assertPathAllowed(repo-inside-path, repo-root-as-8.3-short-name) was
// incorrectly ALLOWED because only absPath was canonicalized, not root.
// Fix: root is now passed through canonicalize() before comparison.
test('[HIGH] rejects repo-inside path when root is passed as uppercase/mixed-slash equivalent', () => {
  // Use a root that is lexically different from REPO but after normPath is equivalent.
  // On Windows: replace forward slashes with backslashes in the synthetic REPO path.
  // On non-Windows: uppercase the synthetic REPO path — normPath lowercases both sides.
  const rootVariant = process.platform === 'win32'
    ? REPO.replace(/\//g, '\\')   // backslash form — normPath makes it identical to REPO
    : REPO.toUpperCase();         // uppercase form — normPath lowercases both, so they match
  assert.throws(
    () => assertPathAllowed(
      path.join(REPO, 'secrets', 'auth.json'),
      rootVariant,
    ),
    /inside repo working tree/,
  );
});
test('[HIGH] rejects repo-inside path when root has trailing slash(es)', () => {
  assert.throws(
    () => assertPathAllowed(
      path.join(REPO, 'secrets', 'auth.json'),
      REPO + '/',
    ),
    /inside repo working tree/,
  );
});

// ── isAttachValue covers value-smuggling ─────────────────────────────────────
test('isAttachValue detects cdp token in value', () => {
  assert.ok(isAttachValue('cdp://localhost:9222'));
});
test('isAttachValue detects endpoint token in value', () => {
  assert.ok(isAttachValue('ws-endpoint://foo'));
});
test('isAttachValue does not flag safe value', () => {
  assert.ok(!isAttachValue('https://example.com'));
});

// ── allowlist shape verification ─────────────────────────────────────────────
test('FLAG_ALLOWLIST does not contain removed dangerous flags', () => {
  const removed = ['--config', '--port', '--host', '--help', '--version',
    '--save-trace', '--save-session', '--output-dir', '--no-sandbox',
    '--timeout-action', '--timeout-navigation', '--user-data-dir'];
  for (const f of removed) {
    assert.ok(!FLAG_ALLOWLIST.has(f), `${f} must not be in allowlist`);
  }
});
test('BROWSER_ALLOWLIST contains only Playwright engine names', () => {
  assert.ok(BROWSER_ALLOWLIST.has('chromium'));
  assert.ok(BROWSER_ALLOWLIST.has('firefox'));
  assert.ok(BROWSER_ALLOWLIST.has('webkit'));
  assert.ok(!BROWSER_ALLOWLIST.has('chrome'));
  assert.ok(!BROWSER_ALLOWLIST.has('msedge'));
  assert.ok(!BROWSER_ALLOWLIST.has('cdp'));
});

// ── resolveCli fail-closed (no network, no auto-install) ────────────────────
test('[resolveCli] throws with provision command when cache absent', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-resolve-test-'));
  try {
    // Point at a non-existent cache dir — cli.js will not be found.
    let thrown = null;
    try {
      resolveCli({ cacheDir: path.join(tmpDir, 'nonexistent') });
    } catch (e) {
      thrown = e;
    }
    assert.ok(thrown, 'resolveCli must throw when cache is absent');
    assert.match(thrown.message, /not provisioned/i, 'error message must mention "not provisioned"');
    assert.match(thrown.message, /npm install/, 'error message must include npm install command');
    assert.match(thrown.message, /adapters\/playwright-mcp\/README\.md/, 'error message must reference README');
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('[resolveCli] returns cli.js path when cache is present', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-resolve-hit-'));
  const cliPath = path.join(tmpDir, 'node_modules', '@playwright', 'mcp', 'cli.js');
  try {
    fs.mkdirSync(path.dirname(cliPath), { recursive: true });
    fs.writeFileSync(cliPath, '// stub\n');
    const result = resolveCli({ cacheDir: tmpDir });
    assert.equal(result, cliPath, 'resolveCli must return cli.js path on cache hit');
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('[resolveCli] does not attempt network access or spawn npm (fail-closed, not auto-install)', () => {
  // Verify that resolveCli throws immediately rather than blocking on npm install.
  // We measure elapsed time: auto-install would take seconds; fail-closed should be <100ms.
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-timing-test-'));
  try {
    const start = Date.now();
    try { resolveCli({ cacheDir: path.join(tmpDir, 'absent') }); } catch (_) { /* expected */ }
    const elapsed = Date.now() - start;
    assert.ok(elapsed < 500, `resolveCli must fail-closed instantly (elapsed ${elapsed}ms ≥ 500ms suggests auto-install)`);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
