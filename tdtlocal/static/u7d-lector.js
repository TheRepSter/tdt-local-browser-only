// Lector de la página pública «Emitido en TV (U7D)» de un canal de Atresplayer, para el modo navegador de TDT local
// (docs/PARIDAD-NAVEGADOR.md, fase 7; el bookmarklet lo monta lib/atres.js y se sirve en /u7d-lector.js).
//
// Se ejecuta DENTRO de la página de Atresplayer (pulsando el marcador «Leer la lista en TDT local» en la ventana
// emergente que TDT local abre, o pegando este código en la consola de esa ventana con F12). Lee lo que la página
// muestra — los enlaces /grabaciones/<canal>/<id>/ con su título, día, hora e imagen — y se lo envía a la ventana de
// TDT local que abrió esta (window.opener) por postMessage; si no hay ventana detrás, abre TDT local con la lista en
// el fragmento de la URL (el fragmento nunca sale del navegador). El origen de TDT local llega en
// window.__TDT_U7D_TO__, que el bookmarklet pone antes de este código.
//
// Qué NO hace: no hace peticiones, no inicia sesión, no pulsa nada, no toca el aviso de cookies y no lee nada fuera de
// esa página. Solo lee y desplaza la página unas pocas veces (como haría una persona) para que cargue más filas, y
// luego la deja arriba. La reproducción de cada programa sigue abriendo la página de Atresplayer.
(function () {
  'use strict';

  const HREF = /^\/grabaciones\/([a-z0-9-]{2,40})\/([0-9a-f]{24})\/?$/i;
  const DIA = /^(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+(\d{1,2})$/i;
  const HORA = /^(\d{1,2}):(\d{2})$/;
  const IMAGEN = /^https:\/\/imagenes\.atresplayer\.com\//;
  const MAX_ITEMS = 200;

  /**
   * Una tarjeta de la página como programa, o `null` si no es una grabación.
   * @param {{href: string, title?: string, aria?: string, lines?: string[], image?: string}} card
   */
  function leerTarjeta(card) {
    const m = HREF.exec(String(card.href || ''));
    if (!m) return null;
    const lines = (card.lines || []).map((l) => String(l).trim()).filter(Boolean);
    const dia = lines.map((l) => DIA.exec(l)).find(Boolean);
    const hora = lines.map((l) => HORA.exec(l)).find(Boolean);
    const aria = String(card.aria || '').replace(/\.\s*[^.]*$/, '').trim(); // «El secreto. Antena 3» -> «El secreto»
    const title = String(card.title || '').trim() || aria || lines.filter((l) => !DIA.test(l) && !HORA.test(l)).pop() || '';
    if (!title) return null;
    return {
      id: m[2].toLowerCase(),
      channel: m[1].toLowerCase(),
      title: title.slice(0, 160),
      day: dia ? Number(dia[2]) : 0,
      time: hora ? hora[1].padStart(2, '0') + ':' + hora[2] : '',
      image: IMAGEN.test(card.image || '') ? card.image : '',
    };
  }

  /** Todas las tarjetas distintas de una lista de enlaces, en el orden en que salen. */
  function leerTarjetas(cards) {
    const seen = new Set();
    const out = [];
    for (const card of cards) {
      const item = leerTarjeta(card);
      if (!item || seen.has(item.id)) continue;
      seen.add(item.id);
      out.push(item);
      if (out.length >= MAX_ITEMS) break;
    }
    return out;
  }

  /** El canal de la página: «/antena3/u7d/» -> «antena3». */
  function slugDe() {
    return (location.pathname.split('/').filter(Boolean)[0] || '').toLowerCase();
  }

  /** base64url de un texto UTF-8, por trozos. */
  function b64url(text) {
    const bytes = new TextEncoder().encode(text);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  /** El fragmento con el que se abre TDT local cuando no hay ventana detrás: «#u7d=<slug>:<datos>». */
  function makeHash(slug, items) {
    return '#u7d=' + slug + ':' + b64url(JSON.stringify(items));
  }

  /** Lee las tarjetas que la página tiene ahora mismo. */
  function leer() {
    const cards = [...document.querySelectorAll('a[href^="/grabaciones/"]')].map((a) => {
      const img = a.querySelector('img.row-item--image') || a.querySelector('img');
      return {
        href: a.getAttribute('href'),
        title: a.getAttribute('title'),
        aria: a.getAttribute('aria-label'),
        lines: (a.innerText || '').split('\n'),
        image: img ? img.currentSrc || img.src : '',
      };
    });
    return leerTarjetas(cards);
  }

  /** Envía un mensaje a la ventana de TDT local (postMessage al que nos abrió; fragmento si no hay). */
  function enviar(tipo, items) {
    const to = String(window.__TDT_U7D_TO__ || '');
    if (!to) {
      console.warn('TDT local: falta window.__TDT_U7D_TO__ (usa el bookmarklet «Leer la lista en TDT local», no copies el lector suelto).');
      return;
    }
    const msg = { source: 'tdt-u7d', slug: slugDe(), type: tipo, items: items || [] };
    if (window.opener) {
      try { window.opener.postMessage(msg, to); } catch (e) { /* el destino no es el esperado */ }
    } else if (tipo === 'done') {
      // sin ventana que nos abriera: se abre TDT local con la lista en el fragmento (nunca sale del PC)
      window.open(to + '/' + makeHash(msg.slug, msg.items), '_blank');
    }
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { leerTarjeta, leerTarjetas, leer, enviar, b64url, makeHash, slugDe };
    return;
  }

  console.info('TDT local: leyendo la página «Emitido en TV» de ' + slugDe() + '… (tarda unos segundos; al terminar se envía la lista a TDT local)');

  let ultimo = '';
  function contar() {
    const items = leer();
    const huella = items.map((i) => i.id + (i.image ? '+' : '')).join(','); // también cuando llega una imagen
    if (items.length && huella !== ultimo) {
      ultimo = huella;
      enviar('items', items);
    }
    return items.length;
  }

  let pendiente = 0;
  new MutationObserver(() => {
    clearTimeout(pendiente);
    pendiente = setTimeout(contar, 800);
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });

  // la página trae unas filas y carga el resto al llegar abajo: se baja unas pocas veces y se vuelve arriba
  async function recorrer() {
    const pausa = (ms) => new Promise((r) => setTimeout(r, ms));
    for (let i = 0; i < 40 && !contar(); i++) await pausa(500); // espera a que la página pinte la primera fila
    for (let i = 0; i < 5; i++) {
      window.scrollTo(0, document.body.scrollHeight);
      await pausa(1500);
      contar();
    }
    window.scrollTo(0, 0);
    await pausa(1000);
    contar();
    enviar('done', leer());
    console.info('TDT local: lista enviada a TDT local (' + leer().length + ' programas). Puedes cerrar esta ventana.');
  }

  if (document.readyState === 'complete') recorrer();
  else window.addEventListener('load', recorrer, { once: true });
}());
