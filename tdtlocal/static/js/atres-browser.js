// Atresmedia's U7D («últimos 7 días») in a normal browser — docs/PARIDAD-NAVEGADOR.md, phase 7.
// The desktop app opens a hidden window over the channel's public page and its plugin reads it; here the page opens
// that same public page in a popup (window.open) and tells the user to run the bookmarklet there; the lector
// (static/u7d-lector.js) sends what it reads by postMessage to this window (or by URL fragment if there is no
// opener). Nothing is saved until the user answers the confirmation dialog, and the playback of a programme still
// opens Atresplayer's own page. In the app, window.tdtDesktop.atres keeps being used instead.
import { el } from './dom.js';
import { askYesNo } from './browser-plugins.js';
import {
  CACHE_MS, Reading, bookmarkletUrl, cleanItems, listUrl, originOk, parseHashU7d, slugOk, watchAddress,
} from './lib/atres.js';

const cache = new Map(); // slug -> {at, items}: the same 10-minute cache the app has
let pending = null; // {slug, reading, popup, timer}
let lectorSource = null;

const lector = () => (lectorSource
  ? Promise.resolve(lectorSource)
  : fetch('/u7d-lector.js', { cache: 'no-store' }).then((r) => r.text()).then((t) => { lectorSource = t; return t; }));

/** The recordings cached for a slug, or null. */
export const cachedItems = (slug) => {
  const hit = cache.get(slug);
  return hit && Date.now() - hit.at < CACHE_MS ? hit.items : null;
};

/** The `tdtDesktop.atres` shape, over a popup + the bookmarklet. */
export const browserAtres = {
  async list(slug) {
    if (!slugOk(slug)) return { error: 'no permitido' };
    const hit = cachedItems(slug);
    if (hit) return { items: hit };
    if (pending) return { error: 'busy' };
    return new Promise((resolve) => {
      const popup = window.open(listUrl(slug), 'tdt-u7d-' + slug, 'width=1100,height=800');
      if (!popup) {
        resolve({ error: 'el navegador ha bloqueado la ventana emergente: permítela para 127.0.0.1 y reintenta' });
        return;
      }
      const reading = new Reading(slug, (result) => {
        if (pending) clearTimeout(pending.timer);
        pending = null;
        if (result.items) cache.set(slug, { at: Date.now(), items: result.items });
        resolve(result);
      });
      pending = { slug, reading, popup, timer: setTimeout(() => reading.end('tiempo agotado'), 120000) };
      popup.addEventListener?.('unload', () => { if (pending && pending.slug === slug) pending.reading.end('ventana cerrada'); });
      showHelp(slug).catch(() => {});
    });
  },

  watch(address) {
    const page = watchAddress(address);
    if (page) window.open(page, '_blank'); // in a browser the recording opens in a tab of the user's browser
    return !!page;
  },
};

/** The lector's messages: only from Atresplayer's exact origin, only for the read in flight. */
function onMessage(event) {
  if (!originOk(event.origin)) return;
  const msg = event.data;
  if (!msg || msg.source !== 'tdt-u7d' || !slugOk(msg.slug) || !pending || msg.slug !== pending.slug) return;
  if (msg.type === 'items') {
    pending.reading.message(msg); // accumulate; nothing is saved yet
    return;
  }
  if (msg.type === 'done') confirmSave(msg.slug, cleanItems(pending.reading.items));
}

/** Before saving a list that arrived: one honest question, like any other approval of the page. */
function confirmSave(slug, items) {
  const reading = pending && pending.slug === slug ? pending.reading : null;
  askYesNo({
    title: `Lista de «${slug}» leída de Atresplayer`,
    lines: [
      { text: `La página pública ${listUrl(slug)} ha enviado ${items.length ? items.length + ' programas' : 'nada'} (títulos, día, hora e imagen; nada más).`, cls: 'approve-head' },
      { text: 'Fuente: atresplayer.com, página «Emitido en TV (U7D)». TDT local no imita a esa web ni llama a ninguna API suya; ver un programa sigue abriendo su página en Atresplayer.', cls: 'dlg-hint' },
      { text: items.length ? `¿Guardar estos ${items.length} programas en «Últimos 7 días»?` : '¿Descartar esta lectura vacía?', cls: 'field-error' },
    ],
    approveLabel: 'Guardar',
  }).then((yes) => {
    if (!reading) return;
    reading.finish(yes && items.length ? { items } : { error: 'sin datos' });
    if (yes && items.length) window.dispatchEvent(new CustomEvent('tdt-atres-cached', { detail: { slug } }));
  });
}

/** The fragment flow: the lector opened this page with `#u7d=…` because it had no opener. */
export function initAtresBrowser() {
  window.addEventListener('message', onMessage);
  const parsed = parseHashU7d(location.hash);
  if (!parsed) return;
  history.replaceState(null, '', location.pathname + location.search); // the data leaves the address bar
  confirmSave(parsed.slug, parsed.items);
}

/** The instructions dialog with the bookmarklet (drag it to the bookmarks bar, then click it in the Atresplayer popup). */
async function showHelp(slug) {
  const src = await lector();
  const url = bookmarkletUrl(location.origin, src);
  const dlg = el('dialog', 'dlg dlg-wide approve-dlg');
  const title = el('h3', '', `Leer la lista de ${slug} en TDT local`);
  dlg.append(title);
  dlg.append(el('p', 'dlg-hint',
    `Se ha abierto la página pública de Atresplayer en otra ventana (${listUrl(slug)}). Para traer su lista «Emitido en TV» a TDT local:`));
  const mark = el('a', 'btn primary', '⤓ Leer la lista en TDT local');
  mark.href = url;
  mark.append(el('p', 'dlg-hint', '(arrástralo a la barra de marcadores y luego púlsalo dentro de la ventana de Atresplayer)'));
  const copy = el('button', 'btn', 'Copiar el código');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(url.slice('javascript:'.length));
      copy.textContent = 'Copiado: pégalo en la consola (F12) de la ventana de Atresplayer';
    } catch { copy.textContent = 'No se pudo copiar: selecciona el enlace del marcador a mano'; }
  });
  const close = el('button', 'btn', 'Cerrar');
  close.type = 'button';
  close.addEventListener('click', () => dlg.close());
  dlg.append(mark, el('p', 'dlg-hint',
    'Si el navegador no deja ejecutar el marcador en esa página, abre la consola de la ventana de Atresplayer (F12), escribe «allow pasting» y pega el código copiado.'),
    el('p', 'dlg-hint',
      'Solo se lee lo que esa página muestra; no se hace ninguna petición desde aquí. Al terminar, TDT local pregunta antes de guardar la lista.'),
    el('div', 'dlg-actions', copy, close));
  document.body.append(dlg);
  dlg.showModal();
  dlg.addEventListener('close', () => dlg.remove());
}
