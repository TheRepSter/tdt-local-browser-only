// Atresmedia's U7D in a normal browser (phase 7, docs/PARIDAD-NAVEGADOR.md): the page-side rules must be the SAME as
// the desktop's (desktop/lib/atres.js), the bookmarklet must be self-contained and deliver the list by postMessage
// (or fragment when there is no opener), and the served lector must read the same cards as the app's plugin.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { TextDecoder, TextEncoder } from 'node:util';

import {
  Reading, SLUGS, allowedAddress, cleanItems, listUrl, watchAddress,
} from '../../desktop/lib/atres.js';

import {
  ATRES_ORIGIN, CACHE_MS, MAX_ITEMS, Reading as BrowserReading, b64url, bookmarkletUrl, cleanItems as browserClean,
  listUrl as browserListUrl, makeHashU7d, originOk, parseHashU7d, slugOk, watchAddress as browserWatch,
} from '../../tdtlocal/static/js/lib/atres.js';

const LECTOR = readFileSync(new URL('../../tdtlocal/static/u7d-lector.js', import.meta.url), 'utf8');
const lectorBox = { module: { exports: {} } };
vm.runInNewContext(LECTOR, lectorBox);
const lector = lectorBox.module.exports;

const pluginBox = { module: { exports: {} } };
vm.runInNewContext(readFileSync(new URL('../../tools/atresplayer-u7d/lector.js', import.meta.url), 'utf8'), pluginBox);
const pluginLector = pluginBox.module.exports;

const plain = (value) => JSON.parse(JSON.stringify(value)); // objects made inside a vm have another prototype

const ID = '6ade9a5c1eef7d0008852eb7';
const IMG = 'https://imagenes.atresplayer.com/atp/clipping/x/386x217.jpg?optimize=low';

const CARDS = [
  { href: `/grabaciones/antena3/${ID}/`, title: 'El secreto', aria: 'El secreto. Antena 3', lines: ['EMITIDO EN TV', 'Domingo 25', '22:10', 'El secreto'], image: IMG },
  { href: `/grabaciones/antena3/${ID}/`, title: 'El secreto (repetida)', lines: ['Domingo 25', '22:10'] }, // same id: dropped
  { href: '/directos/antena3/', title: 'Directo' }, // not a recording
  { href: '/grabaciones/antena3/ESTO-NO-ES-UN-ID/', title: 'Malo' },
  { href: '/grabaciones/lasexta/aaaaaaaaaaaaaaaaaaaaaaaa/', aria: 'Informativo. laSexta', lines: ['Lunes 12', '15:00'], image: 'http://insegura.example/x.jpg' },
];

test('the rules are the same as the desktop app\'s', () => {
  assert.deepEqual(SLUGS, ['antena3', 'lasexta', 'neox', 'nova', 'mega', 'atreseries']);
  assert.equal(browserListUrl('antena3'), listUrl('antena3'));
  assert.equal(browserWatch('https://www.atresplayer.com/grabaciones/Antena3/' + ID.toUpperCase() + '/'), watchAddress('https://www.atresplayer.com/grabaciones/antena3/' + ID + '/'));
  assert.equal(browserWatch('https://www.atresplayer.com/directos/antena3/'), null);
  assert.ok(allowedAddress(listUrl('neox')));
  const raw = [
    { id: ID, title: 'El secreto', day: 25, time: '22:10', image: IMG },
    { id: 'no-valido', title: 'x' },
    { id: ID, title: 'repetido' },
    { id: 'aaaaaaaaaaaaaaaaaaaaaaaa', title: '  ', day: 99, time: '25:99', image: 'http://x/y.jpg' },
    { id: 'bbbbbbbbbbbbbbbbbbbbbbbb', title: 'Otro', day: 3, time: '08:05', image: IMG },
  ];
  assert.deepEqual(browserClean(raw), cleanItems(raw));
  assert.equal(browserClean(raw).length, 2);
  assert.equal(MAX_ITEMS, 200);
  assert.equal(CACHE_MS, 10 * 60 * 1000);
  assert.equal(ATR_ES_ORIGIN(), ATRES_ORIGIN);
});

function ATR_ES_ORIGIN() { return 'https://www.atresplayer.com'; }

test('only Atresplayer\'s exact origin may speak', () => {
  assert.ok(originOk('https://www.atresplayer.com'));
  assert.ok(!originOk('https://atresplayer.com'));
  assert.ok(!originOk('https://www.atresplayer.com.evil.test'));
  assert.ok(!originOk('null'));
  assert.ok(slugOk('nova') && !slugOk('..') && !slugOk('otro'));
});

test('the bookmarklet is self-contained and carries this page\'s origin', () => {
  const url = bookmarkletUrl('http://127.0.0.1:8788', LECTOR);
  assert.ok(url.startsWith('javascript:'));
  assert.ok(url.includes('window.__TDT_U7D_TO__="http://127.0.0.1:8788"'));
  assert.ok(url.includes(LECTOR.trim()));
  assert.ok(!url.includes('fetch('), 'no cross-origin fetch: the code travels inside the bookmarklet');
});

test('the lector reads the same cards as the app\'s plugin', () => {
  for (const card of CARDS) {
    assert.deepEqual(plain(lector.leerTarjeta(card)), plain(pluginLector.leerTarjeta(card)), JSON.stringify(card));
  }
  assert.deepEqual(plain(lector.leerTarjetas(CARDS)), plain(pluginLector.leerTarjetas(CARDS)));
  assert.equal(lector.leerTarjetas(CARDS).length, 2);
});

test('the hash fragment round-trips with accents and cleans what it carries', () => {
  const items = [{ id: ID, title: 'Pérez y el «día» ñoño', day: 25, time: '22:10', image: IMG }, { id: 'malo' }];
  const parsed = parseHashU7d(makeHashU7d('antena3', items));
  assert.equal(parsed.slug, 'antena3');
  assert.equal(parsed.items.length, 1);
  assert.equal(parsed.items[0].title, 'Pérez y el «día» ñoño');
  assert.equal(parseHashU7d('#u7d=otro:' + b64url('[]')), null);
  assert.equal(parseHashU7d('#add=x'), null);
  assert.equal(parseHashU7d('#u7d=antena3:!!!no-base64!!!'), null);
});

/** Run the bookmarklet in a fake Atresplayer page and capture what it sends. */
async function runBookmarklet({ opener }) {
  const sent = [];
  const anchors = CARDS.map((c) => ({
    getAttribute: (k) => (k === 'href' ? c.href : k === 'title' ? c.title : c.aria),
    querySelector: () => (c.image ? { currentSrc: c.image, src: c.image } : null),
    innerText: (c.lines || []).join('\n'),
  }));
  const win = {
    __TDT_U7D_TO__: undefined, // the bookmarklet sets it
    opener: opener ? { postMessage: (msg, to) => sent.push({ msg, to }) } : null,
    scrollTo: () => {},
    addEventListener: () => {},
    open: (url) => sent.push({ url }),
  };
  const ctx = {
    window: win,
    document: { readyState: 'complete', querySelectorAll: () => anchors, body: { scrollHeight: 5000 } },
    location: { pathname: '/antena3/u7d/' },
    MutationObserver: class { observe() {} disconnect() {} },
    setTimeout: (fn) => { fn(); return 0; },
    clearTimeout: () => {},
    console: { info: () => {}, warn: () => {} },
    btoa, atob, TextEncoder, TextDecoder,
    JSON, Math, String, Number, Array, Object, Promise, Set,
  };
  ctx.window.window = win;
  vm.createContext(ctx);
  vm.runInContext(bookmarkletUrl('http://127.0.0.1:8788', LECTOR).slice('javascript:'.length), ctx);
  for (let i = 0; i < 50; i++) await Promise.resolve(); // let the scroll loop's promises settle
  return sent;
}

test('in the popup the lector posts the list to the opener with the right origin', async () => {
  const sent = await runBookmarklet({ opener: true });
  const posts = sent.filter((s) => s.msg);
  assert.ok(posts.length >= 2, 'items + done');
  assert.ok(posts.every((s) => s.to === 'http://127.0.0.1:8788'));
  const done = posts.find((s) => s.msg.type === 'done');
  assert.equal(done.msg.source, 'tdt-u7d');
  assert.equal(done.msg.slug, 'antena3');
  assert.equal(done.msg.items.length, 2);
  assert.equal(done.msg.items[0].id, ID);
  assert.equal(done.msg.items[0].day, 25);
  assert.equal(done.msg.items[0].time, '22:10');
  assert.ok(!sent.some((s) => s.url), 'no fragment window when there is an opener');
});

test('without an opener the lector opens TDT local with the list in the fragment', async () => {
  const sent = await runBookmarklet({ opener: false });
  const done = sent.find((s) => s.msg && s.msg.type === 'done');
  assert.ok(!done, 'no postMessage without opener');
  const opened = sent.filter((s) => s.url);
  assert.equal(opened.length, 1);
  assert.ok(opened[0].url.startsWith('http://127.0.0.1:8788/#u7d=antena3:'));
  const parsed = parseHashU7d(opened[0].url.slice(opened[0].url.indexOf('#')));
  assert.equal(parsed.items.length, 2);
});

test('the Reading accumulates cleaned items and finishes once', () => {
  const results = [];
  const r = new BrowserReading('antena3', (x) => results.push(x));
  r.message({ slug: 'antena3', type: 'items', items: [{ id: ID, title: 'A', day: 1, time: '10:00' }, { id: 'x' }] });
  r.message({ slug: 'lasexta', type: 'items', items: [{ id: 'aaaaaaaaaaaaaaaaaaaaaaaa', title: 'B' }] }); // another channel: ignored
  assert.equal(r.items.length, 1);
  r.end();
  r.end('otra vez');
  assert.deepEqual(results, [{ items: r.items }]);
  const empty = new BrowserReading('nova', (x) => results.push(x));
  empty.end('ventana cerrada');
  assert.deepEqual(results[1], { error: 'ventana cerrada' });
});
