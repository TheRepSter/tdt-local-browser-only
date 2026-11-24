// The page's preferences. Durable copy on the SERVER (`<datos>/prefs.json`, GET/POST /api/prefs) so they survive a
// change of origin — port, --lan, localhost vs 127.0.0.1, an installed PWA — where localStorage would be lost
// (ROADMAP.md). localStorage stays as the boot cache: the state modules read it synchronously at load, then
// initPrefs() syncs both ways (and reloads once if the server had something this origin did not).
//
// store.get/set keep working synchronously; set also queues the write to the server (coalesced per key, flushed
// with keepalive so it survives a quick close). It never throws: no storage, no server — the page still works.

const PREFIX = 'tdt.';
const FLUSH_MS = 250;

let serverOn = false; // false until /api/prefs answers: then writes go to the server too
const queue = new Map(); // key -> last value (coalesced)
let flushTimer = null;

const localGet = (key) => {
  try {
    const v = localStorage.getItem(PREFIX + key);
    return v === null ? undefined : JSON.parse(v);
  } catch { return undefined; }
};

export const store = {
  get(key, fallback) {
    const v = localGet(key);
    return v === undefined ? fallback : v;
  },
  set(key, value) {
    try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* no storage */ }
    if (serverOn) enqueue(key, value);
  },
};

function enqueue(key, value) {
  queue.set(key, value);
  if (!flushTimer) flushTimer = setTimeout(flush, FLUSH_MS);
}

export async function flushPrefs() { // exported for the tests and the pagehide handler
  if (flushTimer) { clearTimeout(flushTimer); }
  await flush();
}

async function flush() {
  flushTimer = null;
  const items = [...queue.entries()];
  queue.clear();
  for (const [key, value] of items) {
    try {
      await fetch('/api/prefs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
        keepalive: true,
      });
    } catch { /* the local copy is already written; the next load will push it */ }
  }
}

if (typeof window !== 'undefined') window.addEventListener('pagehide', () => { if (queue.size) flushPrefs(); });

/**
 * Sync with the server, once per load. Local keys the server lacks are pushed up (self-healing: also covers a
 * write that did not reach the server before a reload); then the server's values are copied down to localStorage,
 * and if anything changed the page reloads once so the synchronous readers at load see them. No loop: after the
 * sync both sides are equal.
 */
export async function initPrefs() {
  let remote = null;
  try {
    const r = await fetch('/api/prefs', { cache: 'no-store' });
    if (r.ok) remote = (await r.json()).prefs || {};
  } catch { remote = null; }
  if (remote === null || typeof remote !== 'object') return; // no server: localStorage only
  serverOn = true;

  const localKeys = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) localKeys.push(k.slice(PREFIX.length));
    }
  } catch { /* no storage */ }
  for (const key of localKeys) {
    if (!(key in remote)) {
      try {
        await fetch('/api/prefs', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, value: localGet(key) }), keepalive: true,
        });
        remote[key] = localGet(key);
      } catch { /* next load retries */ }
    }
  }

  let changed = false;
  for (const [key, value] of Object.entries(remote)) {
    if (JSON.stringify(localGet(key)) !== JSON.stringify(value)) {
      try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); changed = true; } catch { /* no storage */ }
    }
  }
  if (changed) location.reload();
}
