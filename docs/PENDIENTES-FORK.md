# Pendientes del fork navegador-only: qué, por qué y cómo

Ideas ordenadas por valor para este fork, surgidas de [ROADMAP.md](../ROADMAP.md), [HACKEABLE.md](HACKEABLE.md),
[PUNTOS-DE-ENGANCHE.md](PUNTOS-DE-ENGANCHE.md), [PLUGINS-ARQUITECTURA.md](PLUGINS-ARQUITECTURA.md) y
[SECURITY.md](../SECURITY.md). La fase 10 (preferencias en el servidor) ya está hecha; el resto, con su diseño:

## 1. `--lan` con token opcional (`--token`) + cerrar el rebinding de DNS

**Qué:** hoy `--lan` expone todo sin autenticación (con aviso). Con `--token <secreto>` (o `TDT_TOKEN`), el servidor
pide `Authorization: Bearer <token>` en las rutas de cambio y de lectura sensibles. **Por qué:** el fork vive en el
navegador, donde `--lan` es la forma de usarlo desde el móvil/tablet; sin auth, cualquiera de la LAN lee y cambia
todo. **Cómo:** el token se mete por flag/env (nunca en la URL del log); la página lo recibe una vez (el usuario lo
pega o va en el fragmento `#token=…`, que nunca sale del PC) y lo guarda en `sessionStorage`, añadiéndolo como
cabecera. Sin token presente, comportamiento actual. **Rebinding:** en `Net.check_url`, conectar a la IP ya validada
en vez de re-resolver el nombre. **Pruebas:** rutas 401 sin token con `--lan --token`, 200 con él; DNS rebinding con
doble resolución simulada.

## 2. Docs de seguridad y changelog del fork

**Qué:** `SECURITY.md` dice «aplicación de escritorio» y «ventanas nativas» y no describe el modelo real del modo
navegador (diálogos dibujados por la página, con el texto honesto de `lib/approve.js`); `CHANGELOG.md` tiene
«[Sin publicar]» vacío. **Cómo:** sección nueva en SECURITY.md («modo navegador de este fork») con el modelo de
amenazas traducido (qué puede falsificar una página: todo, salvo la huella del código que el servidor re-comprueba)
y entrada en CHANGELOG describiendo la paridad navegador. **Pruebas:** ninguna (docs), pero el smoke verifica que
los enlaces existen.

## 3. Recordatorios de programas con notificación del navegador

**Qué:** marcar un programa de la guía y que avise cuando empiece, con `Notification` (ROADMAP guía). **Por qué:**
tenemos la Notification API desde la fase 8 (`lib/app-install.js`, permiso pedido al usuario, solo cuando la pestaña
no está visible). **Cómo:** los recordatorios viven en `prefs.json` (fase 10, ya en servidor); un `setInterval` en
la página comprueba cada 30 s contra la guía en curso y dispara `sysNotify`; al hacer clic, foco a la ventana y al
canal. **Pruebas:** node con reloj falso; ruta de prefs ya cubierta.

## 4. Banco de plugins hostiles contra el servidor real en modo navegador

**Qué:** un `scripts/plugin-lab/` equivalente al de la app pero contra el servidor Python solo: instala el plugin
hostil por la API (con confirmación), lanza bucle/memoria/inundación/sonda de aislamiento y falla si la página deja de
responder o el plugin escapa. **Por qué:** el criterio de aceptación 2 hoy se prueba en node (la caja del iframe),
pero no de punta a punta contra el servidor que sirve los archivos con su CSP. **Cómo:** script node que arranca
`python -m tdtlocal --port 0`, hace los POST con confirm, y mide. **Pruebas:** el propio script es la prueba;
corre en CI como job aparte.

## 5. Endurecer la CSP de los iframes de plugin

**Qué:** `PLUGIN_POLICY` debería añadir `prefetch-src 'none'`, `Permissions-Policy` restrictiva (cámara, micro,
geolocalización, pago…) y valorar el bloqueo de WebRTC/mDNS (ARQUITECTURA §8 lo marca como «qué falta»: un plugin
aislado podría filtrar IPs locales vía candidatos ICE). **Cómo:** cabeceras en `_serve_plugin_file`; el host del
iframe añade `allow` sin permisos. **Pruebas:** test de cabeceras en las rutas de plugin + sonda hostil que intenta
`RTCPeerConnection`.

## 6. Resto de ranuras de interfaz y teclas por plugin

`tile.bar`, `tile.notice`, `header`, `channel.row` y el registro `keys` (PUNTOS-DE-ENGANCHE §9) son ganchos de página:
**ya funcionan en navegador**; falta implementarlas en el núcleo y probarlas en los dos modos con un plugin de
laboratorio que las reemplaza.

## 7. Registro de reproductores y reproductor propio por plugin (MSE)

Hito 3 de HACKEABLE: migrar la cadena de `Tile.attempt` a `players`/`player.pick` sin cambiar comportamiento.
En el navegador un plugin *full* puede aportar un reproductor con Media Source Extensions (en Electron sin aislar
también, pero aquí encaja con la PWA). Es el hito delicado: los tests de reproducción deben seguir igual.

## 8. Temas: plantillas y temas hijo

TEMAS.md «Qué falta»: plantillas (cambiar la estructura de casilla/fila/cabecera, no solo el aspecto) y temas hijo
(«como este, pero cambia esto»). Son de página: funcionarían igual en los dos modos.

## 9. Plugins Python de servidor desde carpeta (cierre del hito 1)

`tdtlocal/trusted.py` existe y no se usa. Cargar plugins Python de confianza desde una carpeta con aprobación por
huella SHA-256 (igual que los *full*) sería el sustituto del alcance `main` en el navegador. Pendiente de decidir
(ARQUITECTURA §7 / HACKEABLE §7): proceso aparte con token propio vs in-process. **Diseño propuesto:** in-process
pero con el cargador existente y aprobación por huella, documentado como «tanto poder como un full, pero en el
servidor».

## 10. Calidad: mypy en CI, cobertura, pruebas UI headless

ROADMAP calidad: ejecutar `mypy` (las anotaciones ya están) y arreglar lo que salga; medir cobertura (subir la de
`stt.py`); pruebas de interfaz con Chromium headless vía CDP (sin dependencias nuevas).

## No aplica en el navegador (se quedan en la app de escritorio)

Icono de bandeja, atajos globales del SO, firmar instaladores, winget/APT, pywebview, cerrar la ventana para parar
el servidor (en una PWA no se puede detectar de forma fiable; se queda el botón «Salir»), whisper-server como Job de
Windows.
