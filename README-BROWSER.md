# TDT local en el navegador (fork `tdt-local-browser-only`)

Este fork añade un modo a **TDT local** (https://github.com/cibernicola/tdt-local): abrir
`http://127.0.0.1:8788` en un navegador normal (Chrome, Edge, Brave, Firefox) da **todo** lo que da la
aplicación de escritorio Electron. El servidor Python es el único backend y la página el único frontend.

```bash
python -m tdtlocal --open        # abre tu navegador por defecto con la página
# o sin --open y entra a mano en http://127.0.0.1:8788
# para tenerla en ventana propia: botón «Instalar como aplicación» dentro de la página (PWA)
```

Sin instalar nada (`pip` no hace falta: solo biblioteca estándar de Python). La app de escritorio
(`desktop/`) **no se toca ni se rompe**: cuando existe, su camino manda; este modo es el *fallback* del
navegador. En el navegador hay botón **«Instalar como aplicación»** para tenerla en su propia ventana
(PWA), avisos del sistema con permiso, plugins (con permisos y con todo el poder, aprobados por la huella
SHA-256 de su código), temas, plugins de desarrollo, modo seguro, registro, reglas y MCP para IAs,
U7D de RTVE y de Atresmedia (este último con un bookmarklet, ver abajo).

El inventario completo, el plan por fases y **las tres excepciones** (alcance `main` de los plugins,
diálogos de aprobación dibujados por la página en vez de ventanas del sistema, y bandeja/atajos globales
del SO, que no existen en un navegador) están en [docs/PARIDAD-NAVEGADOR.md](docs/PARIDAD-NAVEGADOR.md).
U7D de Atresmedia en el navegador funciona así: al entrar en un canal de Atresplayer se abre su página
pública «Emitido en TV» en una ventana emergente y el bookmarklet «Leer la lista en TDT local» le pasa
los programas a TDT local (postMessage o fragmento de URL), preguntando antes de guardar; ver cada
programa sigue abriendo la página de Atresplayer.

Pruebas: `python -m unittest discover -s tests -t .` y `node --test`.

El detalle de la paridad está en [docs/PARIDAD-NAVEGADOR.md](docs/PARIDAD-NAVEGADOR.md) y los siguientes pasos del fork (ideas con su diseño, de ROADMAP.md y el resto de docs) en [docs/PENDIENTES-FORK.md](docs/PENDIENTES-FORK.md): token para `--lan`, recordatorios con Notification, banco hostil contra el servidor real, más ranuras de interfaz, reproductor propio por plugin…

## Releases de este fork

Las releases de este fork salen de la rama **`main` de tu repo** (`TheRepSter/tdt-local-browser-only`), no del
proyecto original: cada release es un merge de `plugins-navegador` a `main` con una etiqueta
`v<versión>-browser.<n>` (p. ej. `v1.5.0-browser.1`). A diferencia de la app de escritorio **no hay build**:
este modo solo necesita Python (biblioteca estándar) y un navegador, así que la release es un zip de fuente:

```bash
scripts/pack-release.sh v1.5.0-browser.1   # -> dist/tdt-local-v1.5.0-browser.1.zip
# para usarlo: descomprime, entra en la carpeta y «python -m tdtlocal --open»
```

Para traer una actualización del proyecto original antes de release: `scripts/sync-upstream.sh main`
(fetch + merge de `upstream/main`, pruebas en verde y push). El CI corre las dos suites **y** un smoke test
(`scripts/smoke.py`: arranca el servidor de verdad y pide la página, el manifest, el service worker, el lector,
las API y la CSP) en cada push a `main` o `plugins-navegador`.

## Licencia y origen

Este fork es una modificación de **TDT local** (https://github.com/cibernicola/tdt-local), que es
software libre bajo **Apache License 2.0** ([LICENSE](LICENSE)). Como exige el [NOTICE](NOTICE)
(Apache 2.0, sección 4(d)), este fork conserva el aviso de copyright y el NOTICE original, y cita
el proyecto de arriba. TDT local usa las listas públicas de
[TDTChannels](https://github.com/LaQuay/TDTChannels) (Apache 2.0) y reproduce HLS con
[hls.js](https://github.com/video-dev/hls.js) (Apache 2.0, © Dailymotion); los detalles de terceros
están en el NOTICE. TDT local no aloja ni redistribuye contenido: enlaza los directos que las cadenas
publican abiertamente.
