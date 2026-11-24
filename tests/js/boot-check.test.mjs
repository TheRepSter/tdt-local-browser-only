// The boot watchdog: if app.js (or one of its module imports, e.g. a local plugin page blocked by an ad
// blocker) never evaluates, the page is dead but silent. index.html carries a hidden #bootFail banner and
// js/boot-check.js (a separate module graph) reveals it when window.__tdtBooted is missing. No inline code
// anywhere: the page CSP is script-src 'self' / style-src 'self'. The bundled plugin folder must not be named
// «ads»: the usual ad-block filter lists match the /ads/ path and would cut it in the browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (p) => readFileSync(join(root, p), 'utf8');

test('index.html carries the hidden banner, the external sheet and the watchdog, with no inline script', () => {
  const html = read('tdtlocal/static/index.html');
  assert.match(html, /id="bootFail"/);
  assert.match(html, /href="\/boot-check\.css"/);
  assert.match(html, /src="\/js\/boot-check\.js"/);
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/i, 'index.html must not have inline scripts (CSP script-src \'self\')');
});

test('app.js sets the boot flag and the watchdog looks for it without eval', () => {
  assert.match(read('tdtlocal/static/js/app.js'), /window\.__tdtBooted = true/);
  const watch = read('tdtlocal/static/js/boot-check.js');
  assert.match(watch, /__tdtBooted/);
  assert.match(watch, /bootFail/);
  assert.doesNotMatch(watch, /eval\(|new Function/);
});

test('the banner is styled by its own external sheet', () => {
  assert.match(read('tdtlocal/static/boot-check.css'), /#bootFail/);
});

test('the bundled plugins load with a dynamic import (an ad blocker must not be able to kill the boot)', () => {
  const core = read('tdtlocal/static/js/core-plugins.js');
  assert.doesNotMatch(core, /^import\s+\w+\s+from\s+'\.\.\/plugins\//m, 'no static import of a bundled plugin page');
  assert.match(core, /await import\('\.\.\/plugins\//);
  assert.match(core, /export const includedErrors/);
  assert.match(read('tdtlocal/static/js/app.js'), /includedErrors/);
});

test('no bundled plugin folder is named «ads» (the /ads/ path is what ad-block filter lists match)', () => {
  const bundled = readdirSync(join(root, 'tdtlocal/static/plugins'));
  assert.ok(!bundled.includes('ads'), `static/plugins must not contain an «ads» folder: ${bundled}`);
  assert.ok(bundled.includes('adbreak'));
});
