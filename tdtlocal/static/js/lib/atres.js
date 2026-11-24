// Atresmedia's catch-up («Emitido en TV», U7D) in a normal browser — docs/PARIDAD-NAVEGADOR.md, phase 7.
// The desktop app reads the public page of each channel with the plugin «atresplayer-u7d» inside its own explorer
// window (desktop/lib/atres.js has the same rules for that). Here the user opens the same public page in a popup and
// runs a bookmarklet that reads what the page shows and sends it to this window: postMessage to the opener, or the URL
// fragment if there is no opener (the fragment never leaves the PC). Nothing imitates Atresplayer's web and no private
// API is called: only what the public page shows, and the playback still opens Atresplayer's own page.
// tests/js/atres-browser.test.mjs checks that the rules here are the same as the desktop's.

export const PLUGIN_ID = 'atresplayer-u7d';
export const SLUGS = ['antena3', 'lasexta', 'neox', 'nova', 'mega', 'atreseries'];
export const HOSTS = ['www.atresplayer.com', 'atresplayer.com'];
export const MAX_ITEMS = 200;
export const CACHE_MS = 10 * 60 * 1000;

/** The only origin a message about the list may come from. */
export const ATRES_ORIGIN = 'https://www.atresplayer.com';

const ID = /^[0-9a-f]{24}$/;
const TIME = /^\d{2}:\d{2}$/;
const IMAGE = /^https:\/\/imagenes\.atresplayer\.com\//;

export const listUrl = (slug) => `https://www.atresplayer.com/${slug}/u7d/`;

const WATCH = /^https:\/\/www\.atresplayer\.com\/grabaciones\/([a-z0-9-]{2,40})\/([0-9a-f]{24})\/?$/i;

/** The page of a recording on atresplayer.com, normalised, or `null` if `url` is not one. */
export function watchAddress(url) {
  const m = WATCH.exec(String(url || ''));
  return m ? `https://www.atresplayer.com/grabaciones/${m[1].toLowerCase()}/${m[2].toLowerCase()}/` : null;
}

/** May a message event be trusted as Atresplayer's page? Only that exact origin. */
export const originOk = (origin) => origin === ATRES_ORIGIN;

export const slugOk = (slug) => SLUGS.includes(slug);

/** The recordings of a message, well formed, at most `MAX_ITEMS`, without repeats (same rules as the desktop's). */
export function cleanItems(items) {
  const seen = new Set();
  const out = [];
  for (const it of Array.isArray(items) ? items : []) {
    if (!it || typeof it !== 'object' || typeof it.id !== 'string' || !ID.test(it.id) || seen.has(it.id)) continue;
    const title = typeof it.title === 'string' ? it.title.trim().slice(0, 160) : '';
    if (!title) continue;
    seen.add(it.id);
    out.push({
      id: it.id,
      title,
      day: Number.isInteger(it.day) && it.day >= 1 && it.day <= 31 ? it.day : 0,
      time: typeof it.time === 'string' && TIME.test(it.time) ? it.time : '',
      image: typeof it.image === 'string' && it.image.length < 400 && IMAGE.test(it.image) ? it.image : '',
    });
    if (out.length >= MAX_ITEMS) break;
  }
  return out;
}

/**
 * One read of a channel's page: the messages accumulate here, cleaned, until «done».
 */
export class Reading {
  constructor(slug, finish) {
    this.slug = slug;
    this.items = [];
    this.over = false;
    this.finish = (result) => {
      if (this.over) return;
      this.over = true;
      finish(result);
    };
  }

  /** A message of the lector (the caller has already checked the origin and the slug). */
  message(msg) {
    if (this.over || !msg || msg.slug !== this.slug) return;
    if (msg.type === 'items') this.items = cleanItems(msg.items);
  }

  /** Time ran out or the window closed: what was read so far, or why there is nothing. */
  end(reason = 'sin datos') {
    this.finish(this.items.length ? { items: this.items } : { error: reason });
  }
}

/** base64url of UTF-8 text, in chunks (a week of programmes is tens of KB). */
export function b64url(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** The fragment the lector leaves when there is no opener: `#u7d=<slug>:<base64url of the items JSON>`. */
export function makeHashU7d(slug, items) {
  return '#u7d=' + slug + ':' + b64url(JSON.stringify(items));
}

/** Parse that fragment back (and clean what it carries); `null` if it is not one of ours. */
export function parseHashU7d(hash) {
  const m = /^#u7d=([a-z0-9-]{2,40}):([A-Za-z0-9_-]+)$/.exec(String(hash || ''));
  if (!m || !slugOk(m[1])) return null;
  try {
    const bin = atob(m[2].replace(/-/g, '+').replace(/_/g, '/'));
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    return { slug: m[1], items: cleanItems(JSON.parse(new TextDecoder().decode(bytes))) };
  } catch {
    return null;
  }
}

/**
 * The bookmarklet: a `javascript:` URL with the whole lector inside (self-contained, like tools/capturar-directo.js:
 * it does not load anything cross-origin, so no mixed-content or CORS problem). `window.__TDT_U7D_TO__` carries this
 * page's origin, so the lector knows where to postMessage.
 */
export function bookmarkletUrl(tdtOrigin, lectorSource) {
  return 'javascript:window.__TDT_U7D_TO__=' + JSON.stringify(tdtOrigin) + ';\n(function(){\n' + lectorSource + '\n})();';
}
