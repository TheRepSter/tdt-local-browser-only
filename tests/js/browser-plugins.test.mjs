// browser-plugins.js is the browser's half of the desktop IPC: it must answer the settings tab with the same
// shape the desktop app answers with (the fresh list at the top level, {plugins: [...]}) — paintPlugins reads
// result.plugins, and an answer without it crashed activating a plugin («data.plugins is undefined»).

import assert from 'node:assert/strict';

import { test } from 'node:test';

const LIST = { mode: 'browser', plugins: [{ id: 'saludo', name: 'Saludo', enabled: true }] };

function mockFetch(routes) {
  return async (url, init) => {
    const u = String(url);
    for (const [prefix, fn] of routes) {
      if (u.startsWith(prefix)) {
        const data = fn(u, init && init.body ? JSON.parse(init.body) : {});
        return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
      }
    }
    throw new Error('fetch inesperado: ' + u);
  };
}

test('setEnabled resolves with the list at the top level, like the desktop over IPC', async () => {
  globalThis.window = { dispatchEvent() {} };
  globalThis.fetch = mockFetch([
    ['/api/plugins/enable', () => ({ ok: true })],
    ['/api/plugins', () => LIST],
  ]);
  const { browserPlugins } = await import('../../tdtlocal/static/js/browser-plugins.js?set-ok');
  const r = await browserPlugins.setEnabled('saludo', true);
  assert.equal(r.ok, true);
  assert.deepEqual(r.plugins, LIST.plugins, 'top-level .plugins: what paintPlugins iterates');
  assert.deepEqual(r.list.plugins, LIST.plugins, '.list keeps working for the callers that read it');
});

test('a failed activation also answers with the list (no crash painting the result)', async () => {
  globalThis.window = { dispatchEvent() {} };
  globalThis.fetch = mockFetch([
    ['/api/plugins/enable', () => ({ ok: false, detail: 'falta aprobar' })],
    ['/api/plugins', () => LIST],
  ]);
  const { browserPlugins } = await import('../../tdtlocal/static/js/browser-plugins.js?set-err');
  const r = await browserPlugins.setEnabled('saludo', true);
  assert.equal(r.ok, false);
  assert.equal(r.detail, 'falta aprobar');
  assert.deepEqual(r.plugins, LIST.plugins);
});

test('a plugin needing approval asks for the review first (before, enablePlan(res, null) never said «ask» and the checkbox silently reverted)', async () => {
  globalThis.window = { dispatchEvent() {} };
  const calls = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const data = u.startsWith('/api/plugins/enable') ? { ok: false, needsApproval: true, detail: 'necesita aprobación del usuario' }
      : u.startsWith('/api/plugins/review') ? { error: 'no está' }
      : LIST;
    return { ok: true, status: 200, json: async () => data };
  };
  const { browserPlugins } = await import('../../tdtlocal/static/js/browser-plugins.js?needs-approval');
  const r = await browserPlugins.setEnabled('saludo', true);
  assert.ok(calls.some((u) => u.startsWith('/api/plugins/review?id=')), 'the review is fetched when the server asks for approval');
  assert.equal(r.ok, false);
  assert.ok(r.detail, 'a reason is surfaced, not swallowed');
  assert.deepEqual(r.plugins, LIST.plugins);
});

test('rescan resolves with the list itself', async () => {
  globalThis.window = { dispatchEvent() {} };
  globalThis.fetch = mockFetch([
    ['/api/plugins/rescan', () => ({ ok: true, list: LIST })],
    ['/api/plugins', () => LIST],
  ]);
  const { browserPlugins } = await import('../../tdtlocal/static/js/browser-plugins.js?rescan');
  const r = await browserPlugins.rescan();
  assert.deepEqual(r.plugins, LIST.plugins);
});
