# Paridad total en el modo navegador: inventario y plan

> **Rama del fork:** `plugins-navegador` (fork `tdt-local-browser-only`). **Meta:** que abrir
> `http://127.0.0.1:8788` en un navegador normal (Chrome/Edge/Brave/Firefox) dé **todo** lo que da la
> aplicación de escritorio Electron. El servidor Python pasa a ser el único backend y la página el único
> frontend. La app de escritorio (`desktop/`) **no se toca ni se rompe**: cuando existe, su camino manda;
> el nuevo es el *fallback* del navegador.
>
> Este documento es el inventario de la fase 0 y el plan. Las fases 1–9 se van aprobando una a una.
> Dónde chocan con los docs vigentes (`PLUGINS.md`, `HACER-PLUGINS.md`, `DESKTOP.md` dicen hoy «solo en la
> aplicación de escritorio»), aquí se dice; esos textos se actualizan al cerrar (fase 9).

## Cómo se sabe en qué modo se está

Hoy la página distingue los modos por `window.tdtDesktop`, que solo existe cuando carga la app Electron
(`desktop/preload.cjs`). En el navegador no habrá preload: el modo se anuncia por la API del servidor
(p. ej. `GET /api/mode` → `{mode: "browser" | "desktop"}`), y cada pieza nueva del núcleo comprueba
**siempre del lado servidor** lo que puede comprobarse ahí (quién llama, qué permiso tiene), igual que hoy
hace `main.js` con `fromOurPage`.

## Inventario: cada funcionalidad de la app de escritorio, con su estado en el navegador

Leyenda: ✅ ya funciona en navegador · 🔧 a portar (fase) · ⛔ imposible en un navegador normal (y por qué).

### Núcleo y reproducción (la página es la misma en los dos modos)

| Funcionalidad | Navegador |
|---|---|
| Listas TDTChannels al día, proxy HLS, radio, comprobar canales, multivisión, EPG (+ guía extra Atresmedia), YouTube incrustado, canales propios | ✅ ya funciona |
| Cabeceras de canal editables desde la interfaz (botón Cabeceras) | ✅ ya funciona |
| U7D de RTVE (La 1, La 2, 24 Horas, Teledeporte, Clan): lista y reproducción | ✅ ya funciona (`/api/u7d`, `/api/u7d/play`) |
| Ajustes del servidor (todas las pestañas: IA, subtítulos/Whisper, listas y guía, SCTE, durante la publicidad, acceso de IAs) | ✅ ya funciona |
| Asistente de IA local, subtítulos en directo y alertas por voz | ✅ ya funciona |
| Marcas SCTE-35: vigilancia, registro, exportar, aviso en pantalla, reacción durante la publicidad (con stream) | ✅ ya funciona |
| Sonido: igualador de volumen y ecualizador | ✅ ya funciona |
| Puente con el reproductor de Atresplayer (`tools/puente-atresplayer`) como extensión/Tampermonkey del navegador | ✅ ya funciona (no depende de la app) |
| Reglas: botón Reglas, editor, «lo que han hecho», reglas por canal/hora/programa/tecla, reglas que traen los plugins | ✅ ya funciona (las de plugin, cuando haya plugins) |
| Bus de control (estado y órdenes con token; niveles leer/controlar) | ✅ ya funciona en navegador |
| MCP por stdio (`tdt-local mcp`) y `POST /mcp`: las 12 herramientas de lectura y las de control | ✅ ya funciona en navegador |
| Ganchos de la página (`tdt.on/filter/register`), plugin incluido de publicidad, capas `tile.overlay` | ✅ ya funciona |
| Inspector en vivo (eventos y reglas; llamadas de plugins cuando los haya) | ✅ ya es de la página |

### Lo que solo da hoy la app de escritorio

| Funcionalidad (pestaña por pestaña) | Navegador |
|---|---|
| **Ajustes ▸ Plugins**: listar plugins (de scripts, con permisos, con todo el poder), activar/desactivar, problemas del manifest a la vista | 🔧 fase 1+2 |
| Instalar desde archivo `.tdtplugin`, desde enlace https con SHA-256, desde catálogo; **firma de autor** (ed25519, confianza en el primer uso, aviso si la clave cambia); SHA-256 mostrado antes de instalar; instalar no activa | 🔧 fase 1 (la validación se traduce a Python; el diálogo de archivo pasa a `<input type=file>`) |
| Empaquetar para compartir (`.tdtplugin` reproducible + SHA-256) | 🔧 fase 1 |
| «Crear un plugin nuevo», «Crear un tema», «Crear un plugin con todo el poder» | 🔧 fase 3 (contra la API; «abrir la carpeta» ⛔ se sustituye por ver/descargar los archivos por API) |
| Plugins **con permisos** (api 1): marco aislado `sandbox`, panel flotante o pantalla sobre el reproductor, SDK `/@tdt/sdk.js`, permisos declarados y concedidos, quitar un permiso suelto, ajustes generados del manifest, servicios que duermen (`activation`), fuentes declarativas (`tdt.sources` + `embed:`), proveedores de canales y de guía, temas de 12 colores, reglas que trae el plugin, latido y «no responde», límite de ritmo, almacenamiento aislado, `network:<host>` | 🔧 fase 1+2 (iframe `sandbox="allow-scripts"` servido por el servidor en `/plugin/<id>/…` con CSP propia; cada llamada validada contra lo concedido; red y almacenamiento por rutas del servidor) |
| Plugins **con todo el poder** (`tdt.full.page`): corren dentro de la página con los ganchos; aprobación atada a la **huella SHA-256** de todos los archivos; una letra cambiada → se para y se vuelve a preguntar, y se vuelve a comprobar al entregar el código | 🔧 fase 3 (la API entrega código + huella; `core-plugins.js` alimenta el mismo cargador `trusted`) |
| **Temas completos** (`tdt.full.css`): hoja tras `style.css`, imágenes/fuentes incrustadas como `data:`, un tema a la vez | 🔧 fase 3 (por API; `setStyle` ya existe) |
| Plugins **de desarrollo** (`tdt.dev`): modo desarrollador, carpeta propia, alcance `page` con recarga en caliente, aviso rojo permanente | 🔧 fase 4 (alcance `page` sí; ver ⛔ abajo el `main`) |
| Alcance **`main`** de los plugins (Node: archivos, procesos, red sin límite, ventanas, bandeja, atajos globales) | ⛔ **imposible**: en un navegador no hay proceso principal del cliente donde ejecutar código; el servidor Python no ejecuta JS de terceros. Se rechaza el manifest con un mensaje claro (fase 4). El alcance `server` (Python) existe como cargador sin usar (`tdtlocal/trusted.py`) y queda fuera de este fork salvo decisión explícita |
| **Modo seguro**: arranque sin plugins tras dos cierres malos seguidos, `--safe`, botón en Ajustes ▸ Plugins y bandeja, aviso permanente «MODO SEGURO», salir del modo seguro | 🔧 fase 4, con una traducción: en el navegador no hay app que vigile la ventana, así que el **servidor** cuenta en su carpeta de datos los arranques que no terminaron con cierre limpio (`run.json`, igual que `desktop/lib/safe-mode.js` pero del lado Python) y arranca sin plugins; la página muestra el aviso y el botón (safe-banner.js ya existe, leerá el estado de la API) |
| **Ajustes ▸ Registro**: log de la app (arranque, plugins, explorar, servidor, consola de la página, fallos de red una vez por dirección sin parámetros), ocultando claves/tokens/cabeceras de credenciales; filtrar, copiar, guardar en archivo, abrir carpeta | 🔧 fase 6 (API del servidor con el mismo `scrub` de `desktop/lib/logbook.js`; guardar ⛔ con diálogo nativo → descarga del navegador; abrir carpeta ⛔ → no existe) |
| **Ajustes ▸ Desarrollo** y el aviso permanente rojo | 🔧 fase 4 (la pestaña existe; el modo se enciende con confirmación **en la página**, fase 5) |
| **Ajustes ▸ Acceso de IAs** nivel «escribir plugins» y las confirmaciones nativas: `tdt_rules_set`, `tdt_headers_set`, `tdt_plugin_write/read/config/enable/permission`, `tdt_dev_write/activate/logs/list/read` | 🔧 fase 5 (diálogos de aprobación **en la página**, con el texto honesto pedido; el bus ya hace orden→respuesta con esperas de hasta 180 s; una pregunta a la vez) |
| **U7D de Atresmedia**: ventana «Explorar Atresplayer» que abre `/<canal>/u7d/`, el plugin `atresplayer-u7d` lee el DOM y pasa los programas; ventana aparte para ver la grabación | 🔧 fase 7, rediseñado (ver abajo) |
| **Límites de memoria y CPU por plugin** (300 MB / 80 % de un núcleo durante 20 s, medidos por proceso con `app.getAppMetrics`) | 🔧 fase 4, con pérdida: ⛔ en el navegador no hay métricas por proceso; se aproxima con `performance.memory` (solo Chromium), el latido y reinicio del que se cuelga; se documenta la pérdida de precisión frente a Electron |
| **Bandeja**: mostrar, plugins, modo seguro, salir | ⛔ **imposible** en un navegador (fuera de alcance): no hay icono de bandeja ni atajos globales del SO. Se documenta; «Salir» ya existe como botón con `--quit-button` |
| **Ventana propia** (Chromium de Electron), cerrar la pestaña para el servidor, instancia única, puerto recordado (`port.json`) | ⛔ como ventana propia; 🔧 fase 8 lo sustituye por **PWA** (`display: standalone` servido por el propio servidor) + `--open` que abre en modo app si el navegador lo soporta. Cerrar la pestaña no puede parar el servidor: se queda el botón «Salir» |
| Inyección de `content_scripts` en páginas ajenas desde la app (el puente dentro de la ventana) | ⛔ sin extensión del navegador no se puede; el puente ya funciona como extensión (`tools/puente-atresplayer`), y eso no cambia |
| Diálogos nativos del sistema **no falsificables por ninguna página** | ⛔ en el navegador el diálogo lo dibuja la página (fase 5), con el texto pedido: «esta ventana la dibuja la página; en la app de escritorio era una ventana del sistema que ninguna página podía falsificar». Un plugin con todo el poder corre en la misma página y puede imitarlo: es inherente al modelo «corre dentro de la página» que ya acepta HACKEABLE.md |
| `TDT_DEBUG_PORT` / DevTools embebido por marco | ⛔ no aplica (se usan los del navegador) |
| Notificaciones del sistema desde la bandeja | 🔧 fase 8 con la Notification API del navegador (con permiso del usuario), no con la bandeja |

### U7D de Atresmedia en el navegador (fase 7, rediseño)

Electron lo resuelve con una ventana oculta propia donde renderiza `/<canal>/u7d/` y el plugin lee su DOM.
En un navegador eso no existe, así que la alternativa es: **ventana emergente** (`window.open`) a la página
del canal + un **bookmarklet** (patrón de `tools/capturar-directo.js`) que lee la lista de programas que
la propia página muestra y la envía a la ventana de TDT local por `postMessage`/fragmento, con diálogo de
confirmación antes de guardarla. Se mantienen las reglas del plugin original (`tools/atresplayer-u7d`):
no imita a la web ni llama a su API privada, cita su origen y acepta que la reproducción siga abriendo la
página de Atresplayer. Si al prototipar Atresplayer lo impide (CSP, `frame-ancestors`, detección de
automatización), se documenta aquí como excepción y se sigue.

## Plan por fases

Cada fase acaba con sus pruebas en verde (`python -m unittest discover -s tests -t .` y `node --test`)
y un commit pequeño, antes de pasar a la siguiente.

| Fase | Qué | Criterio de salida |
|---|---|---|
| **0** | Este documento: inventario + plan | Plan aprobado |
| **1** | `tdtlocal/plugins.py` + rutas en `server.py`: listar, instalar (.tdtplugin / carpeta / enlace con SHA-256 y firma de autor), desinstalar, activar/desactivar, con la **misma** validación de manifest y permisos que `desktop/lib/plugin-admin.js`/`plugin-api.js` (traducida, no reinventada); servir `/plugin/<id>/…` con CSP estricta por plugin y `frame-ancestors` solo 127.0.0.1; almacenamiento aislado por plugin en la carpeta de datos; ruta de red `network:<host>` con las mismas reglas que el proxy (solo http(s), nunca IPs locales ni de metadatos); huella SHA-256 de los full comprobada en cada entrega | Pruebas de Python de cada ruta; un paquete con `..` o enlace se rechaza; la huella cambiada no se entrega |
| **2** | `plugin-host.js` sin Electron: el iframe `sandbox="allow-scripts"` se sirve de `/plugin/<id>/` (origen opaco) en vez de `tdt-plugin://`; cada llamada por postMessage se valida contra los permisos concedidos (ya lo hace `lib/plugin-host.js`); permisos leídos/otorgados/revocados vía API; pestaña Ajustes ▸ Plugins completa en navegador | `docs/ejemplos` (ahora, tiempo, pantalla, teletexto, saludo) funcionan en Chrome normal; el plugin hostil de `scripts/plugin-lab/plugins/hostile` no escapa de la caja (prueba node) |
| **3** | `core-plugins.js`: sin preload, pide a la API los full activos (código + huella aprobada) y alimenta el mismo cargador `trusted`; huella cambiada = se para y se vuelve a preguntar; temas completos por API insertados tras `style.css` (`setStyle` ya existe); botones «Crear un tema» y «Crear un plugin con todo el poder» contra la API | Un full al que le cambia una letra deja de correr y vuelve a preguntar (prueba) |
| **4** | Dev-plugins servidos por la API con recarga en caliente (ETag o el flujo de eventos del bus); manifest con alcance `main` rechazado en navegador con mensaje claro; modo seguro en navegador (estado en la carpeta de datos del servidor, equivalente a `desktop/lib/safe-mode.js`); inspector asegurado sin bridge; límites mem/CPU factibles en navegador (medición desde el iframe, ralentí y reinicio) y documentada la pérdida de precisión | Modo seguro entra solo tras dos arranques sin cierre limpio; inspector muestra eventos, llamadas y reglas sin `tdtDesktop` |
| **5** | Diálogos de aprobación **en la página** (activar plugin, permisos nuevos, escritura por MCP) con el texto honesto pedido; MCP completo en navegador: `tdt_rules_set`, `tdt_headers_set`, `tdt_plugin_write/read/config/enable/permission`, `tdt_dev_write/activate` esperan la respuesta del diálogo de la página con el mismo patrón orden/respuesta del bus, una pregunta a la vez | `tdt-local mcp` lista las 30 herramientas y una escritura pide confirmación en la página; las cabeceras editables por interfaz siguen funcionando |
| **6** | Ruta de API del log filtrado (ocultando claves/tokens como `desktop/lib/logbook.js`); pestaña Registro completa en navegador: filtrar, copiar, guardar en archivo (descarga) | La pestaña muestra lo mismo que en la app |
| **7** | U7D de Atresmedia sin Electron: `window.open` + bookmarklet que lee la lista y la envía por postMessage/fragmento, con confirmación antes de guardar; reproducción abre la página de Atresplayer; si Atresplayer lo impide, excepción documentada aquí | La lista de un canal de Atresmedia se ve en U7D en Chrome normal, o la excepción queda escrita |
| **8** | PWA: `manifest.json` servido por el servidor (nombre, iconos, `display: standalone`, `start_url`), ajuste «Instalar como aplicación», `--open` en modo app si se puede, favicon y Notification API para los avisos; bandeja y atajos globales documentados como inexistentes | El navegador ofrece instalar la app; los avisos llegan por Notification API |
| **9** | Cierre: este documento actualizado (igual/cambió/las tres excepciones: alcance `main`, diálogos nativos, bandeja/atajos); `README-BROWSER.md` corto citando el proyecto original y su licencia Apache 2.0 (como exige NOTICE); los docs que decían «solo en la app» corregidos | Criterios de aceptación de abajo, todos en verde |

## Estado de las fases

- **Fase 0** hecha: este documento.
- **Fase 1** hecha: `tdtlocal/plugins.py` (lógica pura + `PluginStore`), `tdtlocal/plugins_routes.py` (las rutas HTTP), `tdtlocal/pluginsign.py` (ed25519 de solo-verificar en Python puro, con vectores RFC 8032 en `tests/test_pluginsign.py`) y el cableado en `server.py` (`/plugin/<id>/…` con la CSP propia del plugin y `frame-ancestors` solo loopback; `frame-src` de la página crece con los `embed:` de los plugins activos), `app.py` y `net.py` (`follow_redirects=False` para validar cada salto). El estado del modo navegador vive en `<data>/browser/`; la app de escritorio usa `<data>/desktop/` y no se toca. Pruebas en `tests/test_plugins.py` (45 pruebas).
  - Instalación en dos pasos: `POST /api/plugins/install/inspect` (paquete crudo ≤ 16 MB con firma en `X-TDT-Signature`, o JSON `{url, sha256, signature}`) deja el paquete en `staging/` con un `token` y la revisión; `POST /api/plugins/install/commit {token}` instala. **Instalar nunca activa.**
  - `enable` solo enciende un plugin con permisos o full si está aprobado con la firma exacta de lo que hay en la carpeta; `full_handover()` vuelve a leer el código y a comprobar la huella SHA-256 en cada entrega: si cambió una letra, no se entrega.
  - `/api/plugins/call` es lo que un plugin pide al servidor (storage, config, `net.fetch`); cada llamada se valida otra vez contra los permisos concedidos, y la red pasa por `plugin_fetch` (solo https, solo lo que cubre `network:<host>`, cada redirección revalidada, ≤ 1 MB de respuesta).
  - Nota: el servidor no valida la **sintaxis** del JS de un plugin (en Python no hay motor JS); la página la detecta al ejecutarlo. Manifest, permisos, paquetes, enlaces y huellas sí se validan en el servidor.
- **Fase 2** hecha: `plugin-host.js` ya no se rinde sin Electron — usa el puente IPC de la app cuando existe y `browser-plugins.js` (HTTP) en un navegador. Los iframes `sandbox="allow-scripts"` se sirven de `/plugin/<id>/` (origen opaco) con la CSP propia del plugin; cada llamada del SDK pasa por `/api/plugins/call`, que vuelve a comprobar el permiso en el servidor. La pestaña **Ajustes ▸ Plugins** funciona completa en navegador: listar, activar (con diálogo en la página que enseña permisos o código + huella antes de aprobar, `lib/approve.js` con el texto honesto), quitar permisos sueltos, ajustes del manifest, recargar, empaquetar (descarga del `.tdtplugin` con su SHA-256), instalar desde archivo/enlace/catálogo (inspeccionar → diálogo → commit), crear plugin/tema/full. `GET /api/plugins/review?id=` da a la página la firma y la vista previa para aprobar. Diferencias documentadas: «Abrir la carpeta» no existe en navegador (el navegador no abre carpetas del SO) y los cambios llegan por sondeo cada 4 s en vez de por push IPC. Pruebas: `tests/js/browser-approve.test.mjs` y las rutas en `tests/test_plugins.py`.
- **Fase 3** hecha: `core-plugins.js` pide `/api/plugins/full` (el servidor relee cada plugin y comprueba su huella SHA-256 contra la aprobación en cada entrega) y **importa** cada runner como módulo servido en `/plugin-full/<id>/run.js` — la única forma de ejecutar código bajo `script-src 'self'`, sin `eval` en ningún lado; la ruta vuelve a comprobar la huella en el momento de servir (cambió una letra → 409 y no corre hasta que el usuario lo vuelve a aprobar). Los temas (de ui y full) llegan como texto y entran por `setStyle` (constructable stylesheets) tras `style.css`. Los botones «Crear un tema» y «Crear un plugin con todo el poder» ya funcionan contra la API (fase 2). Pruebas: `tests/js/core-plugins.test.mjs` y `tests/test_plugins_full.py`.

## Requisitos de seguridad (innegociables, heredados del proyecto)

- CSP de la página sin scripts ni estilos inline (hoy: `server.py` manda `script-src 'self'`, `style-src 'self'`; los temas van por constructable stylesheets, no por `<style>`).
- Permisos declarados, enseñados en claro y comprobados **en cada llamada** de cada plugin.
- Proxy y ruta de red de plugins: hosts validados, solo http(s), nunca IPs locales ni de metadatos (`net.py` ya lo hace; la ruta de plugins reutiliza `Net`).
- Solo 127.0.0.1 por defecto; `--lan` sin autenticación y con su aviso.
- Token del bus en tiempo constante (`hmac.compare_digest` ya), regenerable, nunca a disposición de un plugin.
- Ninguna activación ni escritura sin decisión explícita del usuario.
- Nada de CSP laxa, nada de saltar el sandbox, nada de auto-actualizar plugins desde internet.

## Criterios de aceptación del fork

1. `docs/ejemplos` (ahora, tiempo, pantalla, teletexto, saludo) funcionan en Chrome normal.
2. El plugin hostil de las pruebas no escapa de su caja.
3. Un plugin full al que le cambia una letra deja de correr y vuelve a preguntar.
4. El MCP por stdio lista las 30 herramientas y una escritura pide confirmación en la página.
5. Registro, modo seguro, inspector y U7D-Atresmedia (o su alternativa) operables en navegador.
6. La pestaña de Plugins y las de Ajustes muestran lo mismo que en la app de escritorio.
7. `python -m unittest discover -s tests -t .` y `node --test` en verde, con pruebas nuevas de cada fase (Python para las rutas del servidor, node para el host sin Electron).
8. Ninguna prueba que hoy pasa en desktop se rompe.
9. Interfaz en español, tono del README.

## Fuera de alcance

- No se borra ni degrada `desktop/`: la app de escritorio sigue igual (su camino manda cuando existe; el nuevo es el fallback del navegador).
- No se implementan atajos globales del SO ni icono de bandeja en el navegador: no existen.
