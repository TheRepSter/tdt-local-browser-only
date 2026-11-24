// The plugins that come with the application. They are loaded by the same loader as any other plugin with all the power, and use the same hooks:
// the application does by default what they do, so a plugin that wants to do it differently can change or replace it. Each one is in
// tdtlocal/static/plugins/<folder>/ with its plugin.json (the folder of «ads» is «adbreak»: browser ad blockers cut local
// URLs like /plugins/ads/ by name, and the folder name is the URL).
import { trusted } from './hooks.js';
import { setStyle } from './styles.js';

// The bundled page is imported dynamically on purpose: if a browser ad blocker still cuts its local URL, a static
// import would take the whole application down with it (the page loads but no module runs: empty list, dead clicks).
// If it cannot load, the app boots anyway and includedErrors says which plugin is missing and why (app.js shows it).
let ads = null;
export const includedErrors = {};
try {
  ads = (await import('../plugins/adbreak/page.js')).default;
} catch (error) {
  includedErrors.ads = String((error && error.message) || error);
}

export const INCLUDED = ads ? [{ id: 'ads', setup: ads }] : [];

const devSeen = new Map(); // dev plugin id -> fingerprint ya importado (the hot-reload poller keeps it between looks)

/** Start them. They register what they bring at once (their setup is not asynchronous), so what needs them can be started right after. */
export function loadIncluded() {
  for (const { id, setup } of INCLUDED) trusted.load(id, setup);
}

/**
 * The plugins without isolation: in the desktop app (development plugins, docs/PLUGINS.md) the preload runs them and hands each one to the page —
 * `window.tdtPluginHost.load(id, run)`, or a queue (`window.__tdtQueue`) if it got there first. Here they are given to the same loader as the included
 * ones, with their id apart (`dev:<id>` or `full:<id>`, so one can never take the place of an included plugin by having its name), and so they get the same `tdt`.
 *
 * In a browser there is no preload: the page asks the server which full plugins are on and approved (`/api/plugins/full`, whose code it re-checks
 * against the approved fingerprint) and imports each one's runner as a module (`/plugin-full/<id>/run.js`, served only while the fingerprint still
 * matches) — the only way to run code under a `script-src 'self'` policy, no eval anywhere. A theme's style sheet comes as text and goes in after
 * the page's own (js/styles.js).
 *
 * `deps` exists for the tests: `window`, `fetchFull`, `importRun`, `warn`.
 */
export async function startPluginHost(deps = {}) {
  const w = deps.window || (typeof window !== 'undefined' ? window : undefined);
  if (!w) return;
  // `source` says where it comes from: «dev» (a development plugin) or «full» (an installed plugin with all the power)
  const load = (id, run, source = 'dev') => trusted.load((source === 'full' ? 'full:' : 'dev:') + id, run);
  // a theme: its style sheet goes in after the page's own (js/styles.js), under a key of its own so it can be taken away
  const css = (id, text, source = 'dev') => setStyle((source === 'full' ? 'full:' : 'dev:') + id + ':theme', text);
  w.tdtPluginHost = { load, css };
  const waiting = w.__tdtQueue || [];
  w.__tdtQueue = [];
  for (const [id, run, source] of waiting) load(id, run, source);
  const themes = w.__tdtThemes || [];
  w.__tdtThemes = [];
  for (const [id, text, source] of themes) css(id, text, source);
  if (w.tdtDev) return; // the desktop app's preload hands over its own
  const fetchFull = deps.fetchFull || (async () => (await fetch('/api/plugins/full', { cache: 'no-store' })).json());
  const importRun = deps.importRun || ((url) => import(url));
  const warn = deps.warn || console.warn;
  let data;
  try {
    data = await fetchFull();
  } catch {
    return; // without the server nothing full runs; the settings tab says what is approved
  }
  for (const p of (data && data.plugins) || []) {
    if (p.css) css(p.id, p.css, 'full');
    if (p.code) await importRun(`/plugin-full/${encodeURIComponent(p.id)}/run.js`).catch((e) => warn('[TDT plugins]', p.id, e && e.message));
  }
  // development plugins of the browser mode: which are active is asked again every few seconds, and each runner is
  // re-imported when the fingerprint of its folder changes — a file saved on disk is picked up without reloading the page
  const fetchDev = deps.fetchDev || (async () => (await fetch('/api/dev/page', { cache: 'no-store' })).json());
  const pollDev = async () => {
    let list;
    try {
      list = ((await fetchDev()) || {}).plugins || [];
    } catch {
      return;
    }
    const seen = new Set();
    for (const p of list) {
      seen.add(p.id);
      if (devSeen.get(p.id) !== p.hash) {
        devSeen.set(p.id, p.hash);
        await importRun(`/plugin-dev/${encodeURIComponent(p.id)}/run.js?v=${encodeURIComponent(p.hash)}`).catch((e) => warn('[TDT dev]', p.id, e && e.message));
      }
    }
    for (const id of [...devSeen.keys()]) {
      if (!seen.has(id)) { devSeen.delete(id); trusted.unload('dev:' + id); }
    }
  };
  await pollDev();
  const pollMs = deps.pollMs === undefined ? 4000 : deps.pollMs;
  if (pollMs > 0) {
    const timer = setInterval(pollDev, pollMs);
    if (typeof timer.unref === 'function') timer.unref(); // in node tests the interval must not keep the process alive
  }
}
