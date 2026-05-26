#!/usr/bin/env node
// Downloads the platform-specific binary for this package from the matching
// GitHub Release. Templated values: samhcharles/overseer, overseer, __VERSION_SOURCE__.
const fs = require('fs');
const path = require('path');
const https = require('https');

const REPO = 'samhcharles/overseer';       // e.g. samhcharles/overseer
const BIN_NAME = 'overseer';     // e.g. overseer
const pkg = require('../package.json');
const VERSION = pkg.version;

const PLATFORM_MAP = {
  'linux-x64': 'linux-amd64',
  'linux-arm64': 'linux-arm64',
  'darwin-x64': 'darwin-amd64',
  'darwin-arm64': 'darwin-arm64',
  'win32-x64': 'windows-amd64',
};

const key = `${process.platform}-${process.arch}`;
const target = PLATFORM_MAP[key];
if (!target) {
  console.error(`[${pkg.name}] no prebuilt binary for ${key}`);
  process.exit(1);
}

const ext = process.platform === 'win32' ? '.exe' : '';
const assetName = `${BIN_NAME}-${target}${ext}`;
const url = `https://github.com/${REPO}/releases/download/v${VERSION}/${assetName}`;

const outDir = path.join(__dirname, '..', 'bin');
fs.mkdirSync(outDir, { recursive: true });
const outPath = path.join(outDir, `${BIN_NAME}${ext}`);

function fetch(u, dest, redirects = 5) {
  return new Promise((resolve, reject) => {
    https.get(u, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        if (redirects === 0) return reject(new Error('too many redirects'));
        return resolve(fetch(res.headers.location, dest, redirects - 1));
      }
      if (res.statusCode !== 200) {
        return reject(new Error(`HTTP ${res.statusCode} for ${u}`));
      }
      const file = fs.createWriteStream(dest, { mode: 0o755 });
      res.pipe(file);
      file.on('finish', () => file.close(resolve));
      file.on('error', reject);
    }).on('error', reject);
  });
}

console.log(`[${pkg.name}] downloading ${assetName} v${VERSION}…`);
fetch(url, outPath)
  .then(() => {
    fs.chmodSync(outPath, 0o755);
    console.log(`[${pkg.name}] installed → ${outPath}`);
  })
  .catch((err) => {
    console.error(`[${pkg.name}] download failed: ${err.message}`);
    console.error(`  url: ${url}`);
    process.exit(1);
  });
