// Service worker of the installed app (PWA): a pure passthrough. TDT local needs its Python server running, so nothing
// is cached — every request goes to the network as usual. Registering this file is what makes the browser offer
// «Instalar como aplicación» (a manifest alone is not enough in some browsers). docs/PARIDAD-NAVEGADOR.md, phase 8.
'use strict';

self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// the «fetch» handler below deliberately does not answer: the network responds, exactly as without this worker
self.addEventListener('fetch', () => {});
