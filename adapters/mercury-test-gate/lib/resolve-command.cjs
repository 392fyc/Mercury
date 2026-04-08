'use strict';
const fs = require('fs');
const path = require('path');

// Parse single-key YAML-ish: test_command: <value>
function parseConventionFile(filePath) {
  try {
    for (const raw of fs.readFileSync(filePath, 'utf8').split('\n')) {
      const line = raw.trim();
      if (!line || line[0] === '#') continue;
      const i = line.indexOf(':');
      if (i === -1 || line.slice(0, i).trim() !== 'test_command') continue;
      let v = line.slice(i + 1).trim();
      if ((v[0] === '"' && v.slice(-1) === '"') || (v[0] === "'" && v.slice(-1) === "'")) v = v.slice(1, -1);
      return v || null;
    }
  } catch (_) {}
  return null;
}

// Auto-detect: package.json > pyproject.toml > Makefile > Cargo.toml
function autoDetect(cwd) {
  const j = path.join;
  try {
    const pkg = JSON.parse(fs.readFileSync(j(cwd, 'package.json'), 'utf8'));
    const t = pkg.scripts && pkg.scripts.test;
    if (t && !t.startsWith('echo "Error: no test')) return 'npm test';
  } catch (_) {}
  try {
    if (fs.readFileSync(j(cwd, 'pyproject.toml'), 'utf8').includes('[tool.pytest')) return 'python -m pytest';
  } catch (_) {}
  try {
    if (/^test\s*:/m.test(fs.readFileSync(j(cwd, 'Makefile'), 'utf8'))) return 'make test';
  } catch (_) {}
  try { fs.accessSync(j(cwd, 'Cargo.toml')); return 'cargo test'; } catch (_) {}
  return null;
}

function resolveCommand(cwd) {
  return parseConventionFile(path.join(cwd, '.mercury', 'config', 'test-gate.yaml')) || autoDetect(cwd);
}

module.exports = { resolveCommand, parseConventionFile, autoDetect };
