#!/usr/bin/env node
// Tiny launcher that execs the downloaded native binary, forwarding argv,
// stdio, and exit code. Templated: overseer.
const path = require('path');
const { spawnSync } = require('child_process');

const ext = process.platform === 'win32' ? '.exe' : '';
const binPath = path.join(__dirname, `overseer${ext}`);

const result = spawnSync(binPath, process.argv.slice(2), { stdio: 'inherit' });
if (result.error) {
  if (result.error.code === 'ENOENT') {
    console.error(`overseer: binary not found at ${binPath}`);
    console.error('try reinstalling: npm install -g ' + require('../package.json').name);
  } else {
    console.error('overseer:', result.error.message);
  }
  process.exit(1);
}
process.exit(result.status ?? 0);
