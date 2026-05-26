#!/usr/bin/env node
'use strict';
// Mercury adapter: config-gate wrapper for @playwright/mcp (Issue #458 / #154 ADR).
// Enforces ADR §4.2 (deny attach-class), §4.3 (least-privilege), §5.2 (path rules)
// before spawning the upstream MCP server. Fail-closed: absent CLI cache → exit 3
// with a one-time provision command (never auto-installs at runtime).
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const PINNED_VERSION = '0.0.75';
const PINNED = `@playwright/mcp@${PINNED_VERSION}`;
// Minimum allowlist — only flags the stdio MCP server actually needs.
// Removed: --config (cdpEndpoint/isolated:false backdoor), --port/--host (network),
// --help/--version (contaminates JSON-RPC), --save-trace/--save-session/--output-dir
// (artifact paths / auth cookies), --no-sandbox, --user-data-dir (persistent profile).
const FLAG_ALLOWLIST = new Set([
  '--isolated', '--caps', '--storage-state',
  '--allowed-origins', '--blocked-origins', '--headless', '--headed',
  '--browser', '--device', '--viewport-size',
]);
// Tokens that mark a flag as attach/connect-class → hard reject (整类拦截).
const ATTACH_TOKENS = ['cdp', 'endpoint', 'extension', 'connect', 'remote', 'ws-endpoint'];
// --browser value whitelist: Playwright engine names only. Real-browser channels
// (chrome/msedge/chrome-beta/msedge-*) attach to installed browsers with user cookies.
const BROWSER_ALLOWLIST = new Set(['chromium', 'firefox', 'webkit']);
// Flags whose value is a file-system path requiring validation.
const PATH_VALUE_FLAGS = new Set(['--storage-state']);
// Real browser profile fragments (case-insensitive, slash-normalized) → reject.
const PROFILE_FRAGMENTS = [
  'google/chrome/user data', 'microsoft/edge/user data',
  'bravesoftware/brave-browser/user data', 'chromium/user data',
];
function normPath(p) { return String(p).replace(/\\/g, '/').toLowerCase(); }
function isAttachFlag(flag) {
  const name = flag.replace(/^--?/, '').toLowerCase();
  return ATTACH_TOKENS.some((t) => name.includes(t));
}

function isAttachValue(value) {
  const v = String(value).toLowerCase();
  return ATTACH_TOKENS.some((t) => v.includes(t));
}
// Expand %VAR% / $VAR / ${VAR}; hard-fail if any token remains unresolved.
function expandEnv(value, env) {
  let out = String(value)
    .replace(/%([^%]+)%/g, (m, n) => (env[n] !== undefined ? env[n] : m))
    .replace(/\$\{([^}]+)\}/g, (m, n) => (env[n] !== undefined ? env[n] : m))
    .replace(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, n) => (env[n] !== undefined ? env[n] : m));
  if (/%[^%]+%/.test(out) || /\$\{[^}]+\}/.test(out) || /\$[A-Za-z_]/.test(out))
    throw new Error(`unresolved environment variable in path: "${value}" → "${out}"`);
  return out;
}
// Canonicalize: resolve symlinks / 8.3 short names / junctions via realpathSync.native.
// If the full path does not exist, walk up to the nearest existing ancestor and
// re-append the non-existing tail. TOCTOU window is narrow — acceptable for PoC.
function canonicalize(absPath) {
  try { return fs.realpathSync.native(absPath); } catch (_) { /* path tail may not exist */ }
  let cur = absPath;
  const tail = [];
  for (;;) {
    const parent = path.dirname(cur);
    if (parent === cur) break;
    tail.unshift(path.basename(cur));
    cur = parent;
    try { return path.join(fs.realpathSync.native(cur), ...tail); } catch (_) { /* keep walking */ }
  }
  return absPath;
}

// Resolve repo root from adapter __dirname (deterministic; no cwd/git dependency).
function resolveRepoRoot(fromDir) {
  let cur = fromDir || __dirname;
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(cur, '.git'))) return cur;
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return null;
}

// Validate one resolved absolute path against repo-inside + browser-profile rules.
function assertPathAllowed(absPath, root) {
  const n = normPath(canonicalize(absPath));
  if (root) {
    const nr = normPath(canonicalize(root)).replace(/\/+$/, '');
    if (n === nr || n.startsWith(nr + '/'))
      throw new Error(`path inside repo working tree is rejected: ${absPath}`);
  }
  for (const frag of PROFILE_FRAGMENTS)
    if (n.includes(frag)) throw new Error(`path points at a real browser profile (rejected): ${absPath}`);
}

// Core pure gate: validate + normalize args. Throws on any violation.
// Returns the final args array (with --isolated injected if missing).
function buildSafeArgs(rawArgs, opts = {}) {
  const env = opts.env || process.env;
  const root = opts.repoRoot !== undefined ? opts.repoRoot : resolveRepoRoot();
  const out = [];
  let hasIsolated = false;
  for (let i = 0; i < rawArgs.length; i++) {
    const arg = rawArgs[i];
    if (typeof arg !== 'string' || !arg.startsWith('-')) { out.push(arg); continue; }
    const eq = arg.indexOf('=');
    const flag = eq >= 0 ? arg.slice(0, eq) : arg;
    let value = eq >= 0 ? arg.slice(eq + 1) : undefined;
    if (isAttachFlag(flag)) throw new Error(`attach/connect-class flag is rejected (ADR §4.2): ${flag}`);
    if (!FLAG_ALLOWLIST.has(flag)) throw new Error(`flag not in safe allowlist (default-deny): ${flag}`);
    if (flag === '--isolated') hasIsolated = true;
    if (flag === '--browser') {
      if (value === undefined) value = rawArgs[++i];
      if (!value || !BROWSER_ALLOWLIST.has(value.toLowerCase()))
        throw new Error(
          `--browser value "${value}" is not an allowed Playwright engine ` +
          `(allowed: ${[...BROWSER_ALLOWLIST].join(', ')}); ` +
          `real-browser channels (chrome, msedge, cdp, …) are rejected`,
        );
      out.push(eq >= 0 ? `${flag}=${value}` : flag);
      if (eq < 0) out.push(value);
      continue;
    }
    // Value-smuggling guard: scan non-path flag values for attach tokens.
    if (!PATH_VALUE_FLAGS.has(flag) && value !== undefined && isAttachValue(value))
      throw new Error(`flag value contains attach/connect token (rejected): ${flag}=${value}`);
    if (!PATH_VALUE_FLAGS.has(flag) && value === undefined && i + 1 < rawArgs.length) {
      const next = rawArgs[i + 1];
      if (typeof next === 'string' && !next.startsWith('-') && isAttachValue(next))
        throw new Error(`flag value contains attach/connect token (rejected): ${flag} ${next}`);
    }
    if (PATH_VALUE_FLAGS.has(flag)) {
      if (value === undefined) value = rawArgs[++i];
      if (value === undefined) throw new Error(`${flag} requires a value`);
      const abs = path.resolve(expandEnv(value, env));
      if (root === null)
        throw new Error(
          `repo root could not be determined; rejecting path flag ${flag} (fail-closed). ` +
          `Ensure the adapter runs from within the Mercury repo working tree.`,
        );
      assertPathAllowed(abs, root);
      out.push(eq >= 0 ? `${flag}=${abs}` : flag);
      if (eq < 0) out.push(abs);
      continue;
    }
    out.push(arg);
  }
  if (!hasIsolated) out.unshift('--isolated');
  return out;
}

// Resolve the pinned cli.js from the per-user cache. Fail-closed: if absent,
// throw with a one-time provision command. NEVER runs npm install at runtime —
// avoids network access and npm noise on the MCP stdio JSON-RPC channel.
function resolveCli(opts = {}) {
  const cacheDir = opts.cacheDir
    || path.join(os.homedir(), '.cache', 'mercury', 'playwright-mcp', PINNED_VERSION);
  const cli = path.join(cacheDir, 'node_modules', '@playwright', 'mcp', 'cli.js');
  if (fs.existsSync(cli)) return cli;
  throw new Error(
    `${PINNED} not provisioned. Run once:\n` +
    `  npm install --no-save --no-fund --no-audit --prefix "${cacheDir}" ${PINNED}\n` +
    `(see adapters/playwright-mcp/README.md §Setup)`,
  );
}

function main() {
  let safeArgs;
  try {
    safeArgs = buildSafeArgs(process.argv.slice(2));
  } catch (e) {
    process.stderr.write(`[playwright-mcp adapter] config gate rejected launch: ${e.message}\n`);
    process.exit(2);
  }
  let cli;
  try {
    cli = resolveCli();
  } catch (e) {
    process.stderr.write(`[playwright-mcp adapter] ${e.message}\n`);
    process.exit(3);
  }
  // stdio:'inherit' only here — this IS the JSON-RPC channel and must be transparent.
  const child = spawn(process.execPath, [cli, ...safeArgs], { stdio: 'inherit', shell: false });
  child.on('exit', (code, sig) => {
    if (sig) { try { process.kill(process.pid, sig); } catch (_) { process.exit(1); } }
    process.exit(code === null ? 1 : code);
  });
  child.on('error', (e) => {
    process.stderr.write(`[playwright-mcp adapter] spawn failed: ${e.message}\n`);
    process.exit(1);
  });
}

if (require.main === module) main();

module.exports = {
  buildSafeArgs, expandEnv, isAttachFlag, isAttachValue, assertPathAllowed,
  normPath, canonicalize, resolveRepoRoot, resolveCli,
  FLAG_ALLOWLIST, ATTACH_TOKENS, BROWSER_ALLOWLIST, PATH_VALUE_FLAGS, PINNED,
};
