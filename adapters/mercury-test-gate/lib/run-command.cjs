'use strict';
const { spawn } = require('child_process');

function runCommand(command, cwd, timeoutSec = 300) {
  return new Promise((resolve) => {
    const out = [], err = [];
    const child = spawn(command, [], { shell: true, cwd });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, timeoutSec * 1000);
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
