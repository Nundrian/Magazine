const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const patch = fs.readFileSync(path.join(root, 'patches', '0001-magazine-sidebar-launcher.patch'), 'utf8');
const dockerfile = fs.readFileSync(path.join(root, 'Dockerfile'), 'utf8');
const additions = patch
  .split(/\r?\n/)
  .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
  .map((line) => line.slice(1))
  .join('\n');

test('launcher is present in expanded and collapsed sidebars', () => {
  assert.equal((additions.match(/href="\/magazine"/g) || []).length, 2);
  assert.match(additions, /id="sidebar-magazine-button"/);
  assert.match(additions, /id="sidebar-magazine-button-collapsed"/);
  assert.equal((additions.match(/aria-label="Magazine"/g) || []).length, 2);
  assert.match(additions, />Magazine<\/div>/);
});

test('ordinary navigation opens a same-origin new tab safely', () => {
  assert.equal((additions.match(/target="_blank"/g) || []).length, 2);
  assert.equal((additions.match(/rel="noopener noreferrer"/g) || []).length, 2);
  assert.doesNotMatch(additions, /preventDefault|goto\(|window\.open/);
});

test('launcher has no workflow or inference side effect', () => {
  assert.doesNotMatch(additions, /fetch\(|\/api\/magazine|\/start|prepare|inference/i);
  assert.match(additions, /on:click\|stopPropagation/);
});

test('build pins Open WebUI source and runtime to 0.11.3', () => {
  assert.match(dockerfile, /OPEN_WEBUI_SOURCE_COMMIT=2a960a59fe1dbbd35282f0556b3666d81102e781/);
  assert.match(dockerfile, /open-webui:v0\.11\.3@sha256:41daa0cf2561a5d4c8d1ff31ee2a98d93ab4d3ac2605cac69366ff6a3374a933/);
  assert.match(dockerfile, /require\('\.\/package\.json'\)\.version/);
});
