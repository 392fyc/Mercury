'use strict';
const { spawn, execSync } = require('child_process');

// Kill the full child process tree (not just the shell parent) on timeout.
// POSIX: detached spawn creates a new process group; kill(-pid) signals it.
// Windows: taskkill /F /T walks the tree via OS.
function killTree(child) {
  try {
    if (process.platform === 'win32') execSync(`taskkill /F /T /PID ${child.pid}`, { stdio: 'ignore' });
    else process.kill(-child.pid, 'SIGKILL');
  } catch (_) { try { child.kill('SIGKILL'); } catch (_) {} }
}

function runCommand(command, cwd, timeoutSec = 300) {
  return new Promise((resolve) => {
    const out = [], err = [];
    const child = spawn(command, [], { shell: true, cwd, detached: process.platform !== 'win32' });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; killTree(child); }, timeoutSec * 1000);
    child.stdout.on('data', (d) => out.push(d));
    child.stderr.on('data', (d) => err.push(d));
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ exit_code: timedOut ? -1 : (code ?? -1), timed_out: timedOut,
        stdout: Buffer.concat(out).toString(), stderr: Buffer.concat(err).toString() });
    });
    child.on('error', (e) => { clearTimeout(timer); resolve({ exit_code: -1, timed_out: false, stdout: '', stderr: e.message }); });
  });
}

module.exports = { runCommand };
