// «Instalar como aplicación» and the system notifications in a browser (phase 8, docs/PARIDAD-NAVEGADOR.md):
// in the desktop app nothing of this is needed (window.tdtDesktop exists); in a browser the button waits for
// beforeinstallprompt, and the notifications only fire when allowed and the page is not visible.

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { initAppInstall, requestNotificationPermission, sysNotify } from '../../tdtlocal/static/js/lib/app-install.js';

test('without a window (node, or the app) initAppInstall is a no-op that returns a no-op notify', () => {
  const notify = initAppInstall(() => {});
  assert.equal(typeof notify, 'function');
  assert.doesNotThrow(() => notify('t', 'b'));
  assert.equal(sysNotify('t', 'b'), false);
});

test('requestNotificationPermission is «unsupported» where there is no Notification', async () => {
  assert.equal(await requestNotificationPermission(), 'unsupported');
});

/** Install fake browser globals and run fn with them (awaiting it if it is async). */
async function withBrowserGlobals(globals, fn) {
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

const storage = () => {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
};

test('sysNotify fires only when allowed, enabled and the page is hidden', async () => {
  const localStorage = storage();
  const sent = [];
  class Notification {
    static permission = 'granted';
    constructor(title, opts) { sent.push({ title, opts }); }
    close() {}
  }
  await withBrowserGlobals({
    window: { focus: () => {}, Notification },
    document: { hidden: true },
    localStorage,
    Notification,
  }, () => {
    // without the user's checkbox: nothing
    assert.equal(sysNotify('Marca de anuncios', 'x'), false);
    assert.equal(sent.length, 0);
    localStorage.setItem('tdt.sysnotif', 'true');
    assert.equal(sysNotify('Marca de anuncios en La 1', 'empieza la publicidad'), true);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].title, 'Marca de anuncios en La 1');
    assert.equal(sent[0].opts.icon, '/icon.svg');
    // page visible: what is on screen is already on screen
    document.hidden = false;
    assert.equal(sysNotify('otra', 'y'), false);
    // permission denied: nothing
    document.hidden = true;
    Notification.permission = 'denied';
    assert.equal(sysNotify('otra', 'y'), false);
  });
});

test('initAppInstall in a browser wires the button and the checkbox (no desktop bridge)', async () => {
  const localStorage = storage();
  const handlers = {};
  const elements = {
    btnInstall: { hidden: true, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } },
    notifWrap: { hidden: true },
    chkNotif: { checked: false, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } },
  };
  const messages = [];
  class FakeNotification {
    static permission = 'default';
    static requestPermission() { return Promise.resolve('granted'); }
  }
  await withBrowserGlobals({
    window: {
      tdtDesktop: undefined,
      addEventListener: (t, f) => { handlers[t] = f; },
      matchMedia: () => ({ matches: false }),
      navigator: {},
      Notification: FakeNotification,
    },
    document: { getElementById: (id) => elements[id] || null, hidden: false },
    localStorage,
    navigator: { serviceWorker: { register: () => Promise.resolve() } },
    Notification: FakeNotification,
  }, async () => {
    // dom.js's $ reads document.getElementById
    const notify = initAppInstall((t) => messages.push(t));
    assert.equal(typeof notify, 'function');
    assert.equal(elements.notifWrap.hidden, false);
    assert.ok(handlers.beforeinstallprompt, 'waits for the browser install prompt');
    handlers.beforeinstallprompt({ preventDefault() {} });
    assert.equal(elements.btnInstall.hidden, false, 'the button shows when the browser offers the install');
    handlers.appinstalled();
    assert.equal(elements.btnInstall.hidden, true);
    assert.ok(messages.some((m) => m.includes('instalado')), messages.join(' | '));
    // the checkbox asks for permission and remembers it
    elements.chkNotif.checked = true;
    await elements.chkNotif.listeners.change();
    assert.equal(localStorage.getItem('tdt.sysnotif'), 'true');
    assert.ok(messages.some((m) => m.includes('Avisos del sistema activados')), messages.join(' | '));
  });
});
