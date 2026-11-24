// The plugins without isolation in a browser (tdtlocal/static/js/core-plugins.js): the page asks the server which full
// plugins are on and approved and imports each one's runner as a module; the desktop app keeps its preload path.

import assert from 'node:assert/strict';

import { test } from 'node:test';

globalThis.document = { adoptedStyleSheets: [] };
globalThis.CSSStyleSheet = class { replaceSync(css) { this.css = css; } };

const { startPluginHost } = await import('../../tdtlocal/static/js/core-plugins.js');
const { styleKeys } = await import('../../tdtlocal/static/js/styles.js');

const tick = () => new Promise((r) => setTimeout(r, 0));

test('in a browser the full plugins come from the API, each one imported as a module, and the themes go to setStyle', async () => {
  const w = {
    __tdtQueue: [['viejo', async (tdt) => { (w.ran = w.ran || []).push(tdt.id); }, 'full']],
    __tdtThemes: [['tema-viejo', 'a { color: red }', 'dev']],
  };
  const imported = [];
  await startPluginHost({
    window: w,
    fetchFull: async () => ({ plugins: [
      { id: 'x', hash: 'ab'.repeat(32), code: 'tdt.on("e", () => {})', css: 'body { color: red }' },
      { id: 'y', hash: 'cd'.repeat(32), code: '// sin tema' },
    ] }),
    importRun: async (url) => { imported.push(url); },
    warn: () => {},
  });
  await tick();
  assert.ok(w.tdtPluginHost && typeof w.tdtPluginHost.load === 'function', 'the host is there before any import');
  assert.deepEqual(w.__tdtQueue, [], 'la cola que pudiera haber se vacía');
  assert.deepEqual(w.__tdtThemes, []);
  assert.deepEqual(imported, ['/plugin-full/x/run.js', '/plugin-full/y/run.js'], 'cada full se importa como módulo servido por el servidor');
  assert.ok(styleKeys().includes('full:x:theme'), 'el tema del full entra por setStyle, tras style.css');
  assert.ok(styleKeys().includes('dev:tema-viejo:theme'));
  assert.deepEqual(w.ran, ['full:viejo'], 'lo que estaba en cola corre con su id aparte (full:…)');
});

test('with tdtDev (the desktop app) nothing is fetched: the preload hands over its own', async () => {
  const w = { tdtDev: {} };
  let fetched = false;
  await startPluginHost({
    window: w,
    fetchFull: async () => { fetched = true; return { plugins: [{ id: 'x', code: '…' }] }; },
    importRun: async () => { throw new Error('no se importa nada'); },
  });
  assert.ok(!fetched, 'la app de escritorio no usa la API para esto');
  assert.ok(w.tdtPluginHost);
});

test('if the server does not answer, nothing full runs and nothing breaks', async () => {
  const w = {};
  const imported = [];
  await startPluginHost({
    window: w,
    fetchFull: async () => { throw new Error('offline'); },
    importRun: async (url) => { imported.push(url); },
  });
  assert.deepEqual(imported, []);
  assert.ok(w.tdtPluginHost, 'el host queda puesto por si llega algo de todos modos');
});

test('a runner that fails to import is only a warning', async () => {
  const w = {};
  const warnings = [];
  await startPluginHost({
    window: w,
    fetchFull: async () => ({ plugins: [{ id: 'roto', code: '…' }] }),
    importRun: async () => { throw new Error('huella cambiada'); },
    warn: (...a) => warnings.push(a.join(' ')),
  });
  assert.equal(warnings.length, 1);
  assert.ok(warnings[0].includes('roto') && warnings[0].includes('huella cambiada'));
});
