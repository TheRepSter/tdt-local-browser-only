"""HTTP routes of the plugin server (the logic is in ``plugins.py``; docs/PARIDAD-NAVEGADOR.md).

- **The page** (same origin): ``GET /api/plugins`` (the list for the settings), ``/api/plugins/ui`` (what the host
  mounts), ``/api/plugins/full`` (the full-power plugins, code checked against the approval), ``/plugin/<id>/…``
  (the files of a running plugin, with the plugin's own policy and a ``frame-ancestors`` that only allows this
  machine) and ``GET /api/plugins/pack`` (download the ``.tdtplugin``).
- **Only from this machine** (like the settings): every change — enable, approve, permissions, config, create,
  uninstall, install (inspect and commit), catalog downloads and the calls a plugin makes of the server
  (``/api/plugins/call``: storage, config, network).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import TYPE_CHECKING, Any

from . import __version__
from .control import PLUGIN_ID, ControlError, clean_command
from .plugins import FULL_RUN_POLICY, MAX_INSTALL_BYTES, PLUGIN_POLICY, frame_ancestors
from .settings_routes import _from_this_machine

if TYPE_CHECKING:
    from .server import Handler


def _plugin_file_guard(handler: "Handler") -> bool:
    """For ``/plugin/<id>/…``: the Host check like everywhere, and no cross-site request. A sandboxed plugin frame has
    an opaque origin, so its requests are same-site at most; a page from anywhere else is cross-site and refused."""
    host = handler.headers.get("Host", "")
    if handler.app.config.loopback_only:
        try:
            name = (urllib.parse.urlsplit("//" + host).hostname or "").lower()
        except ValueError:
            name = ""
        if name not in ("localhost", "127.0.0.1", "::1"):
            handler._json(403, {"error": "host no permitido"})
            return False
    if handler.headers.get("Sec-Fetch-Site", "same-origin") == "cross-site":
        handler._json(403, {"error": "petición de otro sitio"})
        return False
    origin = handler.headers.get("Origin")
    if origin and origin not in ("null", "http://" + host):
        handler._json(403, {"error": "origen no permitido"})
        return False
    return True


def _serve_plugin_file(handler: "Handler", path: str) -> None:
    parts = urllib.parse.urlsplit(path)
    rest = urllib.parse.unquote(parts.path)[len("/plugin/"):]
    plugin_id, _, file_path = rest.partition("/")
    status, ctype, body = handler.app.plugins.serve(plugin_id, "/" + file_path if file_path else "/")
    handler._csp_sent = True  # the plugin's own policy goes instead of the page's
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Content-Security-Policy", PLUGIN_POLICY + "; frame-ancestors " + frame_ancestors(handler.app.config))
    handler.send_header("Cache-Control", "no-store")
    # the frame has no origin of its own (it is sandboxed), so its modules and fonts are cross-origin to it
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cross-Origin-Resource-Policy", "cross-origin")
    handler.end_headers()
    if body and handler.command != "HEAD":
        handler.wfile.write(body)


def handle_get(handler: "Handler", path: str) -> bool:
    """Answer the plugin routes that read. ``False`` if ``path`` is not one of them."""
    app = handler.app
    if path == "/api/mode":
        handler._json(200, {"mode": "browser", "name": "tdt-local", "version": __version__})
    elif path == "/api/plugins":
        handler._json(200, app.plugins.snapshot())
    elif path == "/api/plugins/ui":
        handler._json(200, {"plugins": app.plugins.ui_plugins()})
    elif path == "/api/plugins/full":
        handler._json(200, {"plugins": app.plugins.full_handover()})
    elif path == "/api/plugins/review":
        if not _from_this_machine(handler):
            handler._json(403, {"error": "solo se puede hacer desde el propio equipo"})
            return True
        pid = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query).get("id", [""])[0][:40]
        result = app.plugins.review_of(pid)
        handler._json(200 if "error" not in result else 400, result)
    elif path == "/api/plugins/catalog":
        if not _from_this_machine(handler):
            handler._json(403, {"error": "solo se puede hacer desde el propio equipo"})
            return True
        url = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query).get("url", [""])[0][:600]
        result = app.plugins.catalog(url) if url else {"error": "falta «url»"}
        handler._json(200 if "error" not in result else 400, result)
    elif path == "/api/plugins/pack":
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
        result = app.plugins.pack((query.get("id", [""])[0])[:40])
        if not result.get("ok"):
            handler._json(400, {"error": result.get("error", "no se pudo empaquetar")})
            return True
        handler._send(200, result["buffer"], "application/zip", extra={
            "Content-Disposition": f'attachment; filename="{result["filename"]}"',
            "X-TDT-SHA256": result["sha256"],
        })
    elif path == "/api/logs":
        # the Registro tab of the settings (read route, like /api/settings): the lines are already scrubbed when written
        browser_dir = app.config.data_dir / "browser"
        handler._json(200, {"lines": app.logbook.lines(1500), "folder": str(browser_dir), "file": "log.txt"})
    elif path == "/api/dev/state":
        handler._json(200, app.dev.snapshot())
    elif path == "/api/dev/page":
        handler._json(200, {"plugins": app.dev.page_plugins()})
    elif path.startswith("/plugin/"):
        if _plugin_file_guard(handler):
            _serve_plugin_file(handler, path)
    elif path.startswith("/plugin-dev/"):
        if _plugin_file_guard(handler):
            plugin_id = urllib.parse.unquote(path[len("/plugin-dev/"):]).split("/")[0][:64]
            status, ctype, body = handler.app.dev.serve_run(plugin_id)
            handler._csp_sent = True
            handler.send_response(status)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Content-Security-Policy", FULL_RUN_POLICY)
            handler.send_header("Cache-Control", "no-store")  # hot reload: the page re-imports when the fingerprint changes
            handler.end_headers()
            if handler.command != "HEAD" and body:
                handler.wfile.write(body)
    elif path.startswith("/plugin-full/"):
        if _plugin_file_guard(handler):
            plugin_id = urllib.parse.unquote(path[len("/plugin-full/"):]).split("/")[0][:40]
            status, ctype, body = handler.app.plugins.serve_full_run(plugin_id)
            handler._csp_sent = True
            handler.send_response(status)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Content-Security-Policy", FULL_RUN_POLICY)
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            if handler.command != "HEAD" and body:
                handler.wfile.write(body)
    else:
        return False
    return True


CHANGE_PATHS = (
    "/api/plugins/rescan", "/api/plugins/enable", "/api/plugins/approve", "/api/plugins/permission",
    "/api/plugins/config", "/api/plugins/create", "/api/plugins/uninstall", "/api/plugins/install/commit",
    "/api/plugins/call", "/api/dev/mode", "/api/dev/approve", "/api/dev/rescan", "/api/safe/restart",
    "/api/plugins/admin", "/api/dev/admin",
)


def _admin_body(handler: "Handler", body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Clean the admin command again (the page that forwards it is code too) and pick the store it belongs to."""
    cmd = body.get("cmd")
    if not isinstance(cmd, dict):
        return 400, {"ok": False, "detail": "falta «cmd»"}
    try:
        cleaned = clean_command(cmd)
    except ControlError as exc:
        return exc.code, {"ok": False, "detail": str(exc)}
    from .pluginadmin import run_dev_admin, run_plugin_admin
    if handler.path == "/api/plugins/admin":
        return 200, run_plugin_admin(handler.app.plugins, cleaned, body.get("confirm") is True)
    return 200, run_dev_admin(handler.app.dev, cleaned, body.get("confirm") is True)


def _post_json(handler: "Handler", path: str) -> bool:
    """The change routes that take a small JSON body. All of them only from this machine."""
    if path not in CHANGE_PATHS:  # before reading the body: another route may still want it
        return False
    if not _from_this_machine(handler):
        handler._json(403, {"ok": False, "error": "solo se puede hacer desde el propio equipo"})
        return True
    app = handler.app
    body = handler._body()
    if path == "/api/plugins/rescan":
        app.plugins.scan()
        handler._json(200, {"ok": True, "list": app.plugins.snapshot()})
    elif path == "/api/plugins/enable":
        r = app.plugins.enable(str(body.get("id") or ""), body.get("on") is True)
        app.logbook.add("plugins", "INFO", f"{r.get('id', '')}: " + ("activado" if body.get("on") is True else "desactivado")
                        + ("" if r.get("ok") else f" · {r.get('detail', '')}"))
        handler._json(200, r)
    elif path == "/api/plugins/approve":
        r = app.plugins.approve(str(body.get("id") or ""), body.get("signature"))
        app.logbook.add("plugins", "INFO", f"{body.get('id')}: aprobado" + ("" if r.get("ok") else f" · {r.get('detail', '')}"))
        handler._json(200, r)
    elif path == "/api/plugins/permission":
        pid, name, on = str(body.get("id") or ""), str(body.get("name") or ""), body.get("on") is True
        r = app.plugins.set_permission(pid, name, on)
        app.logbook.add("plugins", "INFO", f"{pid}: permiso {name} " + ("concedido" if on else "quitado"))
        handler._json(200, r)
    elif path == "/api/plugins/config":
        handler._json(200, app.plugins.set_config(str(body.get("id") or ""), body.get("values")))
    elif path == "/api/plugins/create":
        kind = str(body.get("kind") or "ui")
        r = app.plugins.create(body.get("name"), kind if kind in ("ui", "full", "theme") else "ui")
        app.logbook.add("plugins", "INFO", f"creado {r.get('id', '')} ({r.get('kind', kind)})")
        handler._json(200, r)
    elif path == "/api/plugins/uninstall":
        pid = str(body.get("id") or "")
        r = app.plugins.uninstall(pid)
        app.logbook.add("plugins", "INFO", f"{pid}: desinstalado" + ("" if r.get("ok") else f" · {r.get('detail', '')}"))
        handler._json(200, r)
    elif path == "/api/plugins/install/commit":
        r = app.plugins.commit(body.get("token"))
        app.logbook.add("plugins", "INFO", f"instalado {r.get('id', '')}" + ("" if r.get("ok") else f" · {r.get('detail', r.get('error', ''))}"))
        handler._json(200, r)
    elif path == "/api/plugins/call":
        args = body.get("args")
        handler._json(200, app.plugins.call(str(body.get("id") or ""), str(body.get("method") or ""), args if isinstance(args, list) else []))
    elif path == "/api/dev/mode":
        r = app.dev.set_mode(body.get("on") is True)
        app.logbook.add("dev", "INFO", "modo desarrollador " + ("activado" if body.get("on") is True else "apagado"))
        handler._json(200, r)
    elif path == "/api/dev/approve":
        pid, on = str(body.get("id") or ""), body.get("on") is True
        r = app.dev.approve(pid, on)
        app.logbook.add("dev", "INFO", f"{pid}: " + ("activado" if on else "desactivado"))
        handler._json(200, r)
    elif path == "/api/dev/rescan":
        app.dev.scan()
        handler._json(200, {"ok": True, "view": app.dev.snapshot()})
    elif path == "/api/safe/restart":
        handler._json(200, app.set_safe_mode(body.get("safe") is True))
    elif path in ("/api/plugins/admin", "/api/dev/admin"):
        code, result = _admin_body(handler, body)
        tag = "plugins" if path == "/api/plugins/admin" else "dev"
        cmd = body.get("cmd") if isinstance(body.get("cmd"), dict) else {}
        outcome = "confirmado" if body.get("confirm") is True else ("pendiente de aprobación" if result.get("needsConfirm") else "ejecutado")
        app.logbook.add(tag, "INFO" if result.get("ok") or result.get("needsConfirm") else "WARN",
                        f"admin {cmd.get('do', '')} {cmd.get('id', '')}: {outcome}")
        handler._json(code, result)
    else:
        return False
    return True


def _post_install_inspect(handler: "Handler") -> None:
    """Inspect a package without installing it: the raw body is the ``.tdtplugin`` file the user picked (the ``.sig``
    beside it, if any, comes in the ``X-TDT-Signature`` header), or a JSON body ``{url, sha256, signature}`` for a link."""
    if not _from_this_machine(handler):
        handler._json(403, {"error": "solo se puede hacer desde el propio equipo"})
        return
    handler._body_read = True
    ctype = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        body = handler._body(MAX_INSTALL_BYTES)
        result = handler.app.plugins.inspect_link(body.get("url"), body.get("sha256"), body.get("signature"))
    else:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= MAX_INSTALL_BYTES:
            # drain what came (bounded) so the connection can close cleanly, then refuse
            left = min(length, MAX_INSTALL_BYTES + 1024 * 1024)
            handler.connection.settimeout(5)
            while left > 0:
                chunk = handler.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
            handler._json(413, {"error": "el archivo pesa demasiado (hasta 16 MB)"})
            return
        buffer = handler.rfile.read(length)
        sig_header = (handler.headers.get("X-TDT-Signature") or "")[:4096] or None
        result = handler.app.plugins.inspect_bytes(buffer, sig_header)
    handler._json(200 if "error" not in result else 400, result)


def handle_post(handler: "Handler", path: str) -> bool:
    """Answer the plugin routes that change something. ``False`` if ``path`` is not one of them."""
    if path == "/api/plugins/install/inspect":
        _post_install_inspect(handler)
        return True
    if path == "/api/log":
        _post_log(handler)
        return True
    return _post_json(handler, path)


LOG_SOURCE = re.compile(r"^[^\s:/\\\[\]]{1,31}$")  # a tag like «plugins», «servidor» or «página»; «plugin:<id>» is built below


def _post_log(handler: "Handler") -> None:
    """``POST /api/log``: a line from the page (or for a plugin, ``{source: "plugin", id}``) for the Registro tab.
    Only from this machine, and always scrubbed and cut server-side: a full disk or a hostile page cannot do more
    than fill the log, which rotates at 1 MB."""
    if not _from_this_machine(handler):
        handler._json(403, {"ok": False, "error": "solo se puede hacer desde el propio equipo"})
        return
    body = handler._body()
    source = body.get("source")
    if not isinstance(source, str) or not LOG_SOURCE.match(source):
        handler._json(400, {"ok": False, "error": "«source» falta o no es válido"})
        return
    tag = source
    if source == "plugin":
        pid = str(body.get("id") or "")
        if not PLUGIN_ID.match(pid):
            handler._json(400, {"ok": False, "error": "«id» de plugin no válido"})
            return
        tag = "plugin:" + pid
    level = str(body.get("level") or "INFO")
    if level not in ("INFO", "WARN", "ERROR"):
        level = "INFO"
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        handler._json(400, {"ok": False, "error": "«text» falta"})
        return
    handler.app.logbook.add(tag, level, text[:4000])
    handler._json(200, {"ok": True})
