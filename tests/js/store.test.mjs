// The preferences store (static/js/store.js): durable copy on the server (/api/prefs) so favorites survive a change
// of origin in browser mode; localStorage stays the boot cache; initPrefs() syncs both ways and reloads once if the
// server was ahead. Phase 10 of docs/PARIDAD-NAVEGADOR.md.

import assert from 'node:assert/strict';
import { test } from 'node:test';

// fake browser globals, installed per test (awaited, so async fns finish inside)
async function withGlobals(globals, fn) {
  const saved = {};
  for (const key of Object.keys(globals)) {
    saved[key] = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { value: globals[key], configurable: true, writable: true });
  }
  try { return await fn(); } finally {
    for (const key of Object.keys(globals)) {
      if (saved[key]) Object.defineProperty(globalThis, key, saved[key]);
      else delete globalThis[key];
    }
  }
}

const localStorageFake = (initial = {}) => {
  const map = new Map(Object.entries(initial));
  return {
    get length() { return map.size; },
    key: (i) => [...map.keys()][i] ?? null,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
};

// fetch fake backed by a server-prefs object; records the POSTs
const fetchFake = (serverPrefs, posts = []) => async (url, options = {}) => {
  if (url === '/api/prefs' && (!options.method || options.method === 'GET')) {
    return { ok: true, json: async () => ({ prefs: JSON.parse(JSON.stringify(serverPrefs)) }) };
  }
  if (url === '/api/prefs' && options.method === 'POST') {
    const body = JSON.parse(options.body);
    posts.push(body);
    serverPrefs[body.key] = body.value;
    return { ok: true, json: async () => ({ ok: true }) };
  }
  return { ok: false, json: async () => ({}) };
};

let loadCounter = 0;
const loadStore = () => import(`../../tdtlocal/static/js/store.js?n=${++loadCounter}`); // fresh module per test

test('get/set use localStorage and never throw', async () => {
  await withGlobals({ localStorage: localStorageFake() }, async () => {
    const { store } = await loadStore();
    assert.deepEqual(store.get('favs', []), []);
    store.set('vol', { v: 0.5 });
    assert.deepEqual(store.get('vol', null), { v: 0.5 });
    assert.equal(localStorage.getItem('tdt.vol'), '{"v":0.5}');
  });
  await withGlobals({ localStorage: { getItem() { throw new Error('full'); }, setItem() { throw new Error('full'); } } }, async () => {
    const { store } = await loadStore();
    assert.equal(store.get('x', 'fb'), 'fb');
    assert.doesNotThrow(() => store.set('x', 1));
  });
});

test('initPrefs pushes local keys the server lacks, then copies the server down and reloads once', async () => {
  const ls = localStorageFake({ 'tdt.favs': '["la-1"]', 'tdt.vol': '{"v":0.3}' });
  const serverPrefs = { favs: ['la-1', 'antena3'], panel: true }; // server ahead on favs, has panel
  const posts = [];
  let reloads = 0;
  await withGlobals({
    localStorage: ls,
    fetch: fetchFake(serverPrefs, posts),
    location: { reload: () => { reloads++; } },
  }, async () => {
    const { initPrefs } = await loadStore();
    await initPrefs();
    // local-only key (vol) pushed up; favs was already on the server, so not re-posted
    assert.deepEqual(posts, [{ key: 'vol', value: { v: 0.3 } }]);
    // server values copied down: favs overwritten locally, panel added
    assert.equal(ls.getItem('tdt.favs'), '["la-1","antena3"]');
    assert.equal(ls.getItem('tdt.panel'), 'true');
    assert.equal(reloads, 1, 'one reload because something changed');
  });
});

test('initPrefs does not reload when both sides already agree', async () => {
  const ls = localStorageFake({ 'tdt.favs': '["la-1"]' });
  const serverPrefs = { favs: ['la-1'] };
  let reloads = 0;
  await withGlobals({
    localStorage: ls, fetch: fetchFake(serverPrefs), location: { reload: () => { reloads++; } },
  }, async () => {
    const { initPrefs } = await loadStore();
    await initPrefs();
    assert.equal(reloads, 0);
  });
});

test('initPrefs without server just keeps localStorage (no throw, no reload)', async () => {
  const ls = localStorageFake({ 'tdt.kind': '"radio"' });
  let reloads = 0;
  await withGlobals({
    localStorage: ls,
    fetch: async () => { throw new Error('offline'); },
    location: { reload: () => { reloads++; } },
  }, async () => {
    const { initPrefs, store } = await loadStore();
    await initPrefs();
    assert.equal(store.get('kind', 'tv'), 'radio');
    assert.equal(reloads, 0);
    // and set() does not queue anything when the server never answered
    store.set('kind', 'tv');
    assert.equal(ls.getItem('tdt.kind'), '"tv"');
  });
});

test('set() coalesces per key and flushPrefs posts the last value only', async () => {
  const ls = localStorageFake();
  const serverPrefs = {};
  const posts = [];
  await withGlobals({ localStorage: ls, fetch: fetchFake(serverPrefs, posts) }, async () => {
    const { initPrefs, store, flushPrefs } = await loadStore();
    await initPrefs(); // server reachable and empty: local empty too, no reload
    store.set('vol', { v: 0.1 });
    store.set('vol', { v: 0.2 });
    store.set('vol', { v: 0.3 });
    store.set('panel', true);
    await flushPrefs();
    assert.deepEqual(posts, [{ key: 'vol', value: { v: 0.3 } }, { key: 'panel', value: true }]);
    assert.deepEqual(serverPrefs, { vol: { v: 0.3 }, panel: true });
  });
});
