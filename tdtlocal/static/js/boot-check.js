// Watchdog de arranque. Un bloqueador de anuncios del navegador (Windscribe, uBlock…) puede cortar una ruta
// local por el nombre (p. ej. /plugins/adbreak/page.js): si falla el grafo de módulos de app.js, la página queda
// cargada pero muerta (lista vacía, clics que no responden) y no queda rastro en la página.
// Este módulo es un grafo aparte: si a los 4 s app.js no puso window.__tdtBooted, se enseña el aviso #bootFail.
// Sin eval ni inline: la CSP (script-src 'self') lo prohibiría.
setTimeout(() => {
  if (!window.__tdtBooted) {
    const el = document.getElementById('bootFail');
    if (el) el.hidden = false;
  }
}, 4000);
