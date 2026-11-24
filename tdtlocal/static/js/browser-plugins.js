// The browser mode's half of what the desktop app does over IPC (desktop/preload.cjs): the plugin administration of the
// settings tab and the bridge the plugin host uses (list, call, log, onChange). Every call goes to the server's HTTP API
// (tdtlocal/plugins_routes.py), which checks again who asks and what was approved. The questions the desktop app asks in
// native windows are asked here in a dialog of the page, one at a time, with the honest text (lib/approve.js).
import { api, requestJson, postJson } from './api.js';

import { el } from './dom.js';

import { DEV_INTRO } from './lib/dev-panel.js';

import { HONEST, createQueue, enablePlan, planReview } from './lib/approve.js';

const changed = () => window.dispatchEvent(new Event('tdt-plugins-changed'));

const jsonOf = (res) => res.json().catch(() => ({}));

/* ------------------------------------------------------------ the in-page approval dialog (one at a time) */

const ask = createQueue();

/** Show a planned dialog (lib/approve.js) and resolve with what the user answered. One at a time, always. */
export const askPlan = (plan) => ask(() => new Promise((resolve) => {
  const dlg = el('dialog', 'dlg dlg-wide approve-dlg');
  dlg.setAttribute('aria-labelledby', 'approveTitle');
  const title = el('h3', '', plan.title);
  title.id = 'approveTitle';
  dlg.append(title);
  for (const l of plan.lines) dlg.append(el('p', l.cls || 'dlg-hint', l.text));
  if (plan.code) {
    const pre = el('pre', 'approve-code');
    pre.textContent = plan.code;
    dlg.append(pre);
  }
  const actions = el('div', 'dlg-actions');
  const no = el('button', 'btn', 'Cancelar');
  no.type = 'button';
  const yes = el('button', 'btn primary', plan.approveLabel);
  yes.type = 'button';
  if (plan.danger) yes.classList.add('danger');
  const done = (ok) => { dlg.close(); dlg.remove(); resolve(ok); };
  no.addEventListener('click', () => done(false));
  yes.addEventListener('click', () => done(true));
  dlg.addEventListener('cancel', () => done(false));
  actions.append(no, yes);
  dlg.append(actions);
  document.body.append(dlg);
  dlg.showModal();
  yes.focus();
}));

/** A yes/no question in the page (the desktop app asks it in a system window): lines are ``{text, cls}`` or strings. */
export function askYesNo({ title, lines = [], approveLabel = 'Aceptar', danger = false, code = '' }) {
  return askPlan({
    title,
    lines: [...lines.map((l) => (typeof l === 'string' ? { text: l } : l)), { text: HONEST, cls: 'dlg-hint' }],
    code,
    approveLabel,
    danger,
  });
}

function showReview(review, kind) {
  return askPlan(planReview(review, kind));
}

/* ------------------------------------------------------------ the settings tab's plugin administration (the tdtDesktop.plugins shape) */

async function list() {
  const r = await api('/api/plugins');
  return r.data;
}

async function setEnabled(id, on) {
  const res = await postJson('/api/plugins/enable', { id, on }).then(jsonOf);
  let plan = enablePlan(res, null);
  if (plan.action === 'ask') {
    const rev = await requestJson(`/api/plugins/review?id=${encodeURIComponent(id)}`).then((r) => r.data).catch(() => null);
    plan = enablePlan(res, rev);
    if (plan.action === 'ask') {
      const yes = await showReview({ ...rev, installs: 'Al aceptarlo se activa con exactamente lo que ves aquí.' }, 'activate');
      if (!yes) return { ok: false, detail: 'no se ha activado', list: await list() };
      const ap = await postJson('/api/plugins/approve', { id, signature: rev.signature }).then(jsonOf);
      if (!ap.ok) return { ...ap, list: await list() };
      const res2 = await postJson('/api/plugins/enable', { id, on: true }).then(jsonOf);
      changed();
      return res2.list ? res2 : { ...res2, list: await list() };
    }
  }
  if (plan.action === 'error') return { ok: false, detail: plan.message, list: await list() };
  changed();
  return res.list ? res : { ...res, list: await list() };
}

async function inspectAndMaybeCommit(body, ctype, extraHeaders) {
  const res = await fetch('/api/plugins/install/inspect', {
    method: 'POST',
    headers: { 'Content-Type': ctype, ...(extraHeaders || {}) },
    body,
  });
  const review = await res.json().catch(() => ({}));
  if (!res.ok || review.error) return { error: review.error || `el servidor contestó ${res.status}` };
  const yes = await showReview(review, 'install');
  if (!yes) return { error: 'No se instaló: se canceló.' };
  const done = await postJson('/api/plugins/install/commit', { token: review.token }).then(jsonOf);
  changed();
  if (!done.list) done.list = await list();
  return done;
}

export const browserPlugins = {
  list,
  setEnabled,
  rescan: async () => {
    const r = await postJson('/api/plugins/rescan', {}).then(jsonOf);
    changed();
    return r.list || (await list());
  },
  openFolder: () => null, // a browser cannot open a folder of the file system
  create: async (name, kind) => {
    const r = await postJson('/api/plugins/create', { name, kind }).then(jsonOf);
    changed();
    return r;
  },
  install: () => new Promise((resolve) => {
    const input = el('input');
    input.type = 'file';
    input.accept = '.tdtplugin,application/zip';
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) { resolve({ error: 'No se eligió ningún archivo.' }); return; }
      const buffer = await file.arrayBuffer();
      resolve(await inspectAndMaybeCommit(buffer, 'application/tdtplugin'));
    });
    input.click();
  }),
  installLink: (url, sha, signature) => inspectAndMaybeCommit(JSON.stringify({ url, sha256: sha || undefined, signature }), 'application/json'),
  catalog: async (url) => {
    const r = await requestJson(`/api/plugins/catalog?url=${encodeURIComponent(url)}`).catch(() => ({ data: null }));
    return r.data || { error: 'No se pudo leer el catálogo.' };
  },
  setPermission: async (id, name, on) => {
    const r = await postJson('/api/plugins/permission', { id, name, on }).then(jsonOf);
    changed();
    return r.list || (await list());
  },
  setConfig: async (id, changes) => {
    const r = await postJson('/api/plugins/config', { id, values: changes }).then(jsonOf);
    if (r.ok) window.dispatchEvent(new CustomEvent('tdt-plugin-config', { detail: { id, values: r.values } }));
    return r;
  },
  pack: async (id) => {
    const res = await fetch(`/api/plugins/pack?id=${encodeURIComponent(id)}`, { cache: 'no-store' });
    if (!res.ok) return { error: (await res.json().catch(() => ({}))).error || 'no se pudo empaquetar' };
    const blob = await res.blob();
    const name = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || `${id}.tdtplugin`;
    const a = el('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    return { saved: name, sha256: res.headers.get('X-TDT-SHA256') || '' };
  },
  admin: (cmd) => adminFlow('/api/plugins/admin', cmd),
};

/* ------------------------------------------------------------ the plugin host's bridge (the tdtDesktop.ui shape) */

export const browserUi = {
  list: async () => {
    const r = await api('/api/plugins/ui');
    return (r.data && r.data.plugins) || [];
  },
  call: (id, method, args) => postJson('/api/plugins/call', { id, method, args: Array.isArray(args) ? args : [] }).then(jsonOf),
  log: (id, level, text) => { postJson('/api/log', { source: 'plugin', id, level, text }).catch(() => { /* the log is a courtesy */ }); },
  onChange(callback) {
    let last = '';
    let busy = false;
    const check = async () => {
      if (busy) return;
      busy = true;
      try {
        const plugins = await browserUi.list();
        const s = JSON.stringify(plugins);
        if (last && s !== last) callback(false);
        last = s;
      } catch { /* next time */ }
      busy = false;
    };
    const timer = setInterval(check, 4000); // in the desktop app the change arrives pushed over IPC; here it is polled
    window.addEventListener('tdt-plugins-changed', check);
    window.addEventListener('focus', check);
    return () => { clearInterval(timer); };
  },
  onConfig(callback) {
    window.addEventListener('tdt-plugin-config', (e) => callback(String(e.detail.id), e.detail.values));
  },
};

/** Reload the page (the desktop app reloads its window). */
export const reloadPage = () => window.location.reload();

/* ------------------------------------------------------------ the log (the tdtDesktop.logs shape): Ajustes ▸ Registro */

export const browserLogs = {
  // the server keeps the log in <datos>/browser/log.txt, already scrubbed; there is no folder to open in a browser
  get: async () => {
    const r = await requestJson('/api/logs').then((x) => x.data).catch(() => null);
    if (!r || !Array.isArray(r.lines)) return { lines: [], folder: '' };
    return { lines: r.lines, folder: r.file ? r.folder + '/' + r.file : r.folder };
  },
  // the desktop app asks where to save with a system dialog; a browser can only download the file
  save: async () => {
    const data = await browserLogs.get();
    const mode = await api('/api/mode').then((r) => r.data).catch(() => null);
    const head = 'TDT local ' + (mode && mode.version ? mode.version : '') + ' · modo navegador (' + navigator.userAgent + ')';
    const text = [head, ''].concat(data.lines).join('\n') + '\n';
    const a = el('a', '', '');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    a.download = 'tdt-local-registro.txt';
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    return { saved: a.download };
  },
};

/* ------------------------------------------------------------ the development plugins tab (the tdtDesktop.dev shape) */

export const browserDev = {
  state: async () => (await api('/api/dev/state')).data,
  rescan: async () => {
    const r = await postJson('/api/dev/rescan', {}).then(jsonOf);
    window.dispatchEvent(new Event('tdt-dev-changed'));
    return r.view || r;
  },
  openFolder: () => null, // a browser cannot open a folder of the file system
  setMode: async (on) => {
    if (!on) {
      const r = await postJson('/api/dev/mode', { on: false }).then(jsonOf);
      window.dispatchEvent(new Event('tdt-dev-changed'));
      return r.view || r;
    }
    // turning it on ends in a question: in the app it is a system window, here it is this page's dialog
    const yes = await askYesNo({
      title: 'Activar el modo desarrollador',
      lines: [{ text: DEV_INTRO, cls: 'field-error' }, { text: 'Los plugins de desarrollo corren SIN aislamiento: pueden leer y cambiar cualquier cosa de la página. Solo actívalo si sabes lo que haces.' }],
      approveLabel: 'Activar el modo desarrollador',
      danger: true,
    });
    if (!yes) return { ok: false, detail: 'no se activó', view: (await api('/api/dev/state')).data };
    const r = await postJson('/api/dev/mode', { on: true }).then(jsonOf);
    window.dispatchEvent(new Event('tdt-dev-changed'));
    return r.view || r;
  },
  approve: async (id, on) => {
    const view = (await api('/api/dev/state')).data;
    if (!on) {
      const r = await postJson('/api/dev/approve', { id, on: false }).then(jsonOf);
      window.dispatchEvent(new Event('tdt-dev-changed'));
      return r.view || r;
    }
    const p = (view && view.plugins || []).find((x) => x.id === id) || { id, name: id, scopes: [], preview: '' };
    const yes = await askYesNo({
      title: `Activar «${p.name || id}» (plugin de desarrollo, sin aislar)`,
      lines: [
        { text: `${p.name || id}${p.version ? ' ' + p.version : ''} — corre dentro de la página${p.scopes && p.scopes.length ? ' (alcance: ' + p.scopes.join(', ') + ')' : ''}.`, cls: 'approve-head' },
        { text: 'Puede leer y cambiar cualquier cosa de la página de TDT local. Al aceptarlo se activa tal como está ahora; si cambian los alcances que declara, se te volverá a preguntar.', cls: 'field-error' },
      ],
      code: p.preview || '',
      approveLabel: 'Activar',
      danger: true,
    });
    if (!yes) return { ok: false, detail: 'no se activó', view };
    const r = await postJson('/api/dev/approve', { id, on: true }).then(jsonOf);
    window.dispatchEvent(new Event('tdt-dev-changed'));
    return r.view || r;
  },
  admin: (cmd) => adminFlow('/api/dev/admin', cmd),
};

/* ---- MCP admin commands (plugin_*, dev_*): one at a time, every question in this page's dialog ---- */

const adminQueue = createQueue();

async function adminFlow(path, cmd) {
  return adminQueue(async () => {
    let r = await postJson(path, { cmd, confirm: false }).then(jsonOf).catch(() => ({ ok: false, detail: 'el servidor local no responde' }));
    if (r && r.needsConfirm) {
      const ask = r.ask || {};
      let yes;
      if (ask.review) yes = await showReview({ ...ask.review, installs: 'Al aceptarlo se aplica exactamente lo que ves aquí.' }, 'activate');
      else yes = await askYesNo({ title: ask.message || '¿Continuar?', lines: [{ text: ask.detail || '' }], approveLabel: 'Aceptar', danger: true });
      if (!yes) return { ok: false, detail: 'el usuario no ha aceptado' };
      r = await postJson(path, { cmd, confirm: true }).then(jsonOf).catch(() => ({ ok: false, detail: 'el servidor local no responde' }));
    }
    return r;
  });
}

/** «Reiniciar en modo seguro» / «Salir del modo seguro» in a browser: the server is told and the page reloads. */
export const safeRestart = async (safe) => {
  const r = await postJson('/api/safe/restart', { safe }).then(jsonOf).catch(() => ({ ok: false }));
  reloadPage();
  return r;
};
