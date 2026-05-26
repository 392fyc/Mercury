#!/usr/bin/env node
'use strict';
// Manual local Slice B verification. NOT run by node --test / CI.
// Requires the package provisioned (README §Setup) + network access to example.com.
// Run: node adapters/playwright-mcp/slice-b-verify.cjs

const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const readline = require('node:readline');

// Paths — no hard-coding
const repoRoot = path.resolve(__dirname, '..', '..');
const launchCjs = path.join(__dirname, 'launch.cjs');

// Use a unique temporary directory per run to avoid filename collisions,
// prevent concurrent runs from clobbering each other, and eliminate
// predictable paths for credential-containing state files.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mercury-pw-'));
const tmpState = path.join(tmpDir, 'state.json');

// Tracking exported file path for cleanup
let exportedRepoFile = null;

// ─── Minimal MCP stdio JSON-RPC client ───────────────────────────────────────

function spawnMcpServer(extraArgs) {
  const args = [launchCjs, '--caps=storage', '--headless', ...extraArgs];
  const child = spawn(process.execPath, args, {
    cwd: repoRoot,
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: false,
    // POSIX: detached=true puts the child in its own process group so that
    // killTree can send SIGKILL to the whole group (-pid), killing the
    // Chromium sub-tree. On Windows detached is false; taskkill /T covers
    // the tree. stdio remains 'pipe' (not unref'd) so we can read stdout/stderr.
    detached: process.platform !== 'win32',
  });
  return child;
}

class McpClient {
  constructor(child) {
    this.child = child;
    this.nextId = 1;
    this.pending = new Map(); // id → {resolve, reject, timer}
    this.initialized = false;

    const rl = readline.createInterface({ input: child.stdout });
    rl.on('line', (line) => {
      if (!line.trim()) return;
      let msg;
      try { msg = JSON.parse(line); } catch (_) { return; }
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve, reject, timer } = this.pending.get(msg.id);
        clearTimeout(timer);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`RPC error ${msg.error.code}: ${msg.error.message}`));
        else resolve(msg.result);
      }
    });

    child.stderr.on('data', (d) => {
      // surface adapter/server stderr but don't treat as fatal
      process.stderr.write('[server stderr] ' + d.toString());
    });
  }

  _send(obj) {
    this.child.stdin.write(JSON.stringify(obj) + '\n');
  }

  request(method, params, timeoutMs = 90000) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`RPC timeout after ${timeoutMs}ms for method ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this._send({ jsonrpc: '2.0', id, method, params });
    });
  }

  notify(method, params) {
    this._send({ jsonrpc: '2.0', method, params });
  }

  async initialize() {
    const result = await this.request('initialize', {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'mercury-slice-b-verify', version: '0.0.1' },
    });
    this.notify('notifications/initialized', {});
    this.initialized = true;
    return result;
  }

  async toolCall(name, toolArgs) {
    const result = await this.request('tools/call', { name, arguments: toolArgs });
    return result;
  }

  close() {
    try { this.child.stdin.end(); } catch (_) {}
    // clear any pending timers
    for (const { timer, reject } of this.pending.values()) {
      clearTimeout(timer);
      reject(new Error('client closed'));
    }
    this.pending.clear();
  }
}

// ─── Assertion helpers ────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function assert(condition, label) {
  if (condition) {
    console.log(`  PASS  ${label}`);
    passed++;
  } else {
    console.log(`  FAIL  ${label}`);
    failed++;
  }
}

function assertToolResult(result, label) {
  if (!result || result.isError) {
    console.log(`  FAIL  ${label} — tool returned error: ${JSON.stringify(result)}`);
    failed++;
    return false;
  }
  passed++;
  console.log(`  PASS  ${label}`);
  return true;
}

// ─── Cleanup helper ───────────────────────────────────────────────────────────

function cleanup() {
  // Remove exported file that landed in repo sandbox
  if (exportedRepoFile) {
    try { fs.unlinkSync(exportedRepoFile); } catch (_) {}
    exportedRepoFile = null;
  }
  // Remove unique tmpdir (contains state.json with auth cookies); rmSync
  // with recursive+force handles both the file and directory in one call.
  try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
}

// ─── Step A: V1 + handshake + caps ───────────────────────────────────────────

async function stepA() {
  console.log('\n=== Step A: V1 navigate + screenshot + caps ===');
  const client = new McpClient(spawnMcpServer([]));
  try {
    const initResult = await client.initialize();
    assert(!!initResult.serverInfo, 'serverInfo present');
    console.log(`  server version: ${initResult.serverInfo && initResult.serverInfo.version}`);

    // Verify tools list contains required tools
    const toolsResult = await client.request('tools/list', {});
    const toolNames = (toolsResult.tools || []).map((t) => t.name);
    console.log(`  tools count: ${toolNames.length}`);
    assert(toolNames.includes('browser_navigate'), 'tools includes browser_navigate');
    assert(toolNames.includes('browser_take_screenshot'), 'tools includes browser_take_screenshot');
    assert(toolNames.includes('browser_storage_state'), 'tools includes browser_storage_state');

    // V1: navigate
    const navResult = await client.toolCall('browser_navigate', { url: 'https://example.com' });
    assertToolResult(navResult, 'V1 browser_navigate example.com');

    // V1: screenshot
    const ssResult = await client.toolCall('browser_take_screenshot', {});
    assertToolResult(ssResult, 'V1 browser_take_screenshot call');
    const content = ssResult && ssResult.content;
    const imageItem = Array.isArray(content) && content.find((c) => c.type === 'image');
    assert(!!imageItem, 'V1 screenshot result contains image item');
    assert(imageItem && imageItem.mimeType === 'image/png', 'V1 screenshot mimeType is image/png');
    assert(imageItem && typeof imageItem.data === 'string' && imageItem.data.length > 0, 'V1 screenshot data non-empty');

  } finally {
    client.close();
    await waitForExit(client.child);
  }
}

// ─── Step B: V2/V6 storageState round-trip ───────────────────────────────────

async function stepB() {
  console.log('\n=== Step B: V2/V6 storageState round-trip ===');

  // S1: fresh session — set localStorage + cookie + sessionStorage, then export
  console.log('\n  --- S1: set state + export storageState ---');
  const client1 = new McpClient(spawnMcpServer([]));
  let stateFilePath = null;
  try {
    await client1.initialize();

    await client1.toolCall('browser_navigate', { url: 'https://example.com' });

    const lsSetResult = await client1.toolCall('browser_localstorage_set', {
      key: 'mercury_auth', value: 'tok123',
    });
    assertToolResult(lsSetResult, 'S1 browser_localstorage_set mercury_auth');

    const cookieSetResult = await client1.toolCall('browser_cookie_set', {
      name: 'msess', value: 'cookieval', domain: 'example.com', path: '/',
    });
    assertToolResult(cookieSetResult, 'S1 browser_cookie_set msess');

    const ssSetResult = await client1.toolCall('browser_sessionstorage_set', {
      key: 'ephemeral', value: 'should_not_persist',
    });
    assertToolResult(ssSetResult, 'S1 browser_sessionstorage_set ephemeral');

    // Export storageState — no filename arg; server writes to <cwd>/.playwright-mcp/
    const exportResult = await client1.toolCall('browser_storage_state', {});
    assertToolResult(exportResult, 'S1 browser_storage_state export call');

    // Parse the path from the result text (markdown link pattern: ](<path>.json))
    const resultText = exportResult && exportResult.content &&
      exportResult.content.map((c) => c.text || '').join('');
    const match = resultText && resultText.match(/\]\(([^)]+\.json)\)/);
    assert(!!match, 'S1 storageState result contains path markdown link');

    if (match) {
      const captured = match[1];
      const resolved = path.isAbsolute(captured)
        ? path.normalize(captured)
        : path.resolve(repoRoot, captured);
      // Path-traversal guard: constrain to repo sandbox + require .json extension.
      // Prevents a malicious server response from directing reads/deletes outside
      // the designated .playwright-mcp/ directory.
      const sandboxDir = path.resolve(repoRoot, '.playwright-mcp') + path.sep;
      // Windows paths are case-insensitive — compare case-folded there so a
      // case-variant prefix cannot slip past the sandbox guard.
      const inSandbox = process.platform === 'win32'
        ? resolved.toLowerCase().startsWith(sandboxDir.toLowerCase())
        : resolved.startsWith(sandboxDir);
      if (!inSandbox || path.extname(resolved) !== '.json') {
        throw new Error(`Invalid exported storageState path: ${captured}`);
      }
      stateFilePath = resolved;
      exportedRepoFile = stateFilePath; // track for cleanup
      console.log(`  exported to: ${stateFilePath}`);
    }
  } finally {
    client1.close();
    await waitForExit(client1.child);
  }

  if (!stateFilePath) {
    console.log('  FAIL  Cannot continue Step B — exported path not found');
    failed++;
    return;
  }

  // Read the exported state file and validate V6 assertions
  console.log('\n  --- V6 storageState content assertions ---');
  assert(fs.existsSync(stateFilePath), 'V6 exported file exists on disk');

  let stateJson;
  try {
    stateJson = JSON.parse(fs.readFileSync(stateFilePath, 'utf8'));
  } catch (e) {
    console.log(`  FAIL  V6 cannot parse state file: ${e.message}`);
    failed++;
    return;
  }

  // V6: localStorage present
  const origins = stateJson.origins || [];
  const lsEntry = origins.find((o) =>
    o.localStorage && o.localStorage.find((e) => e.name === 'mercury_auth')
  );
  assert(!!lsEntry, 'V6 storageState contains mercury_auth (localStorage)');

  // V6: cookie present
  const cookies = stateJson.cookies || [];
  const sessCookie = cookies.find((c) => c.name === 'msess');
  assert(!!sessCookie, 'V6 storageState contains msess (cookie)');

  // V6: sessionStorage NOT present (ephemeral key should be absent)
  const hasEphemeral = origins.some((o) =>
    JSON.stringify(o).includes('ephemeral') ||
    JSON.stringify(o).includes('should_not_persist')
  );
  assert(!hasEphemeral, 'V6 storageState does NOT contain sessionStorage ephemeral key');

  // Copy to tmpdir (repo-external path — gate allows loading from here).
  // chmod 0o600 restricts read to owner only (auth cookies in file).
  fs.copyFileSync(stateFilePath, tmpState);
  try { fs.chmodSync(tmpState, 0o600); } catch (_) { /* best effort on Windows */ }
  assert(fs.existsSync(tmpState), 'V6 state copied to tmpdir for S2 load');

  // S2: load state from tmpState, verify restoration
  console.log('\n  --- S2: load storageState + verify restoration ---');
  const client2 = new McpClient(spawnMcpServer([`--storage-state=${tmpState}`]));
  try {
    await client2.initialize();

    await client2.toolCall('browser_navigate', { url: 'https://example.com' });

    const lsGetResult = await client2.toolCall('browser_localstorage_get', {
      key: 'mercury_auth',
    });
    assertToolResult(lsGetResult, 'S2 browser_localstorage_get mercury_auth call');
    const lsText = lsGetResult && lsGetResult.content &&
      lsGetResult.content.map((c) => c.text || '').join('');
    assert(lsText && lsText.includes('tok123'), 'S2 mercury_auth value is tok123 (V2 localStorage restored)');

    const cookieListResult = await client2.toolCall('browser_cookie_list', {
      domain: 'example.com',
    });
    assertToolResult(cookieListResult, 'S2 browser_cookie_list call');
    const cookieText = cookieListResult && cookieListResult.content &&
      cookieListResult.content.map((c) => c.text || '').join('');
    assert(cookieText && cookieText.includes('msess'), 'S2 msess cookie present (V2 cookie restored)');

  } finally {
    client2.close();
    await waitForExit(client2.child);
  }
}

// ─── Utility: wait for child to exit, force-killing the whole tree if needed ──

// Force-kill a child AND its descendants. The MCP server spawns a Chromium tree;
// killing only the node process would orphan the browser. On Windows use
// `taskkill /T` (tree); on POSIX the child was spawned with detached=true
// (own process group), so send SIGKILL to -pid (the whole process group).
function killTree(child) {
  if (!child || child.exitCode !== null) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      // -child.pid targets the process group (all descendants including Chromium).
      // Fallback to single-process kill if the group signal fails.
      try {
        process.kill(-child.pid, 'SIGKILL');
      } catch (_) {
        try { child.kill('SIGKILL'); } catch (_) {}
      }
    }
  } catch (_) { /* best effort */ }
}

function waitForExit(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) { resolve(); return; }
    let done = false;
    child.once('exit', () => { done = true; resolve(); });
    // Give it up to 5s to exit gracefully after stdin closed, then force-kill the
    // tree so no orphaned MCP-server / Chromium processes leak between runs.
    setTimeout(() => { if (!done) { killTree(child); resolve(); } }, 5000);
  });
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log('Mercury playwright-mcp Slice B verification');
  console.log(`repo root: ${repoRoot}`);
  console.log(`tmpDir:    ${tmpDir}`);

  try {
    await stepA();
    await stepB();
  } catch (err) {
    console.error('\nUnexpected error:', err.message);
    failed++;
  } finally {
    console.log('\n=== Cleanup ===');
    cleanup();
    // Confirm .playwright-mcp contents removed
    const sandboxDir = path.join(repoRoot, '.playwright-mcp');
    if (fs.existsSync(sandboxDir)) {
      const remaining = fs.readdirSync(sandboxDir);
      if (remaining.length === 0) {
        console.log('  .playwright-mcp/ is empty (clean)');
      } else {
        // Remove remaining artifacts recursively — the server may write nested
        // dirs (e.g. traces/), and unlinkSync cannot remove subdirectories, so
        // any sensitive nested artifact would otherwise survive. rmSync recursive
        // + explicit warning on failure (do not silently swallow a leak).
        try {
          fs.rmSync(sandboxDir, { recursive: true, force: true });
          console.log(`  Removed ${remaining.length} entries from .playwright-mcp/ (recursive)`);
        } catch (e) {
          console.log(`  WARN: could not fully clean .playwright-mcp/ — sensitive artifacts may remain: ${e.message}`);
        }
      }
    } else {
      console.log('  .playwright-mcp/ not present (clean)');
    }
    const tmpGone = !fs.existsSync(tmpDir);
    console.log(`  tmpdir removed: ${tmpGone}`);
  }

  console.log(`\n${'='.repeat(50)}`);
  console.log(`Results: ${passed} PASS, ${failed} FAIL`);
  if (failed === 0) {
    console.log('ALL PASS — V1 navigate+screenshot, V2 storageState auth reuse, V6 sessionStorage exclusion verified.');
    process.exit(0);
  } else {
    console.log('SOME TESTS FAILED');
    process.exit(1);
  }
}

main();
