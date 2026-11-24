"""Development plugins of the browser mode (``desktop/lib/dev.js`` translated; docs/PARIDAD-NAVEGADOR.md, phase 4).

They run WITHOUT isolation or permissions, with the same powers as the page — that is what they are for (trying things, or
letting an AI that edits your files try things) — so what is built here is not a sandbox but the guard rails: only from
their own folder (``TDT_DEV_PLUGINS_DIR`` or ``<data>/browser/dev-plugins/``), only while the developer mode is on, and
each one approved by hand, again if the scopes it declares change. Its code may change freely: hot reload — the page
re-imports the runner when the fingerprint of the folder changes. In a browser only the ``page`` scope exists: ``main``
runs in the app's own process, which no page can have, so a manifest that asks for it is listed with a clear problem and
never runs. The state lives in ``<data>/browser/dev.json``.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .plugins import MAX_SCRIPT_BYTES, fingerprint, inside_of

if TYPE_CHECKING:
    from .config import Config

DEV_ID = re.compile(r"^[\w.-]{1,64}$")
SCOPES = ["page"]  # «main» exists only in the desktop app's own process


def read_dev_plugin(dir_path: Path) -> dict[str, Any]:
    """Read one folder as a development plugin: ``{"tdt": {"api": 0, "dev": {"page": "page.js"}}}``. Always returns a
    dict with ``id``, ``dir``, ``scopes``, ``files`` and ``problems``; one with problems is listed but never runs."""
    dir_path = Path(dir_path)
    plugin_id = dir_path.name
    plugin: dict[str, Any] = {
        "id": plugin_id, "dir": str(dir_path), "name": plugin_id, "version": "", "description": "",
        "scopes": [], "files": {}, "problems": [],
    }
    if not DEV_ID.match(plugin_id):
        plugin["problems"].append("el nombre de la carpeta solo puede llevar letras, números, punto, guion y guion bajo")
    try:
        manifest = json.loads((dir_path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        plugin["problems"].append(f"manifest.json no se puede leer: {exc}")
        return plugin
    if not isinstance(manifest, dict):
        plugin["problems"].append("manifest.json no es un objeto")
        return plugin
    plugin["name"] = str(manifest.get("name") or plugin_id)[:120]
    plugin["version"] = str(manifest.get("version") or "")
    plugin["description"] = str(manifest.get("description") or "")[:400]
    tdt = manifest.get("tdt")
    dev = tdt.get("dev") if isinstance(tdt, dict) else None
    if not isinstance(dev, dict):
        plugin["problems"].append('no es un plugin de desarrollo: le falta «tdt.dev» en el manifest')
        return plugin
    if tdt.get("api") != 0:
        plugin["problems"].append('«tdt.api» tiene que ser 0 (la API de desarrollo no tiene garantías)')
    for key in dev:
        if key == "main":
            plugin["problems"].append("el alcance «main» no está disponible en un navegador: corre en el proceso propio de la aplicación de escritorio, y una página no puede tenerlo")
        elif key not in SCOPES:
            plugin["problems"].append(f"el alcance «{key}» no existe (en un navegador solo hay: {', '.join(SCOPES)})")
    for scope in SCOPES:
        if dev.get(scope) is None:
            continue
        real = inside_of(dir_path, dev[scope])
        if not real:
            plugin["problems"].append(f'el archivo «{dev[scope]}» del alcance «{scope}» no existe o está fuera de la carpeta')
        elif real.stat().st_size > MAX_SCRIPT_BYTES:
            plugin["problems"].append(f'el archivo «{dev[scope]}» es demasiado grande')
        else:
            plugin["scopes"].append(scope)
            plugin["files"][scope] = str(real)
    if not plugin["scopes"] and not plugin["problems"]:
        plugin["problems"].append('no declara ningún alcance en «tdt.dev» (en un navegador: «page»)')
    return plugin


def dev_signature(plugin: dict[str, Any]) -> str:
    """What the user approves: this plugin with these scopes. Changing the scopes changes it, so it is asked again."""
    return f"{plugin['id']}|{','.join(sorted(plugin['scopes']))}"


class DevStore:
    """The development plugins of the browser mode: the folder, the developer mode, what is approved, and the runner
    module the page imports for each active one."""

    def __init__(self, config: "Config") -> None:
        base = Path(config.data_dir) / "browser"
        self.dir = Path(os.environ.get("TDT_DEV_PLUGINS_DIR") or (base / "dev-plugins"))
        self.state_file = base / "dev.json"
        self.lock = threading.RLock()
        self.plugins: list[dict[str, Any]] = []
        self.mode = False
        self.approved: dict[str, str] = {}
        self.safe = ""  # safe mode text: nothing dev runs and nothing changes
        self.scan()

    def scan(self) -> None:
        with self.lock:
            try:
                names = sorted(d.name for d in os.scandir(self.dir) if d.is_dir())
            except OSError:
                names = []
            found: dict[str, dict[str, Any]] = {}
            for name in names:
                folder = self.dir / name
                if (folder / "manifest.json").exists() and name not in found:
                    found[name] = read_dev_plugin(folder)
            self.plugins = list(found.values())
            self._read_state()

    def _read_state(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.mode = isinstance(data, dict) and data.get("mode") is True
        approved = data.get("approved") if isinstance(data, dict) else None
        self.approved = {k: v for k, v in approved.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(approved, dict) else {}

    def _write_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"mode": self.mode, "approved": self.approved}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    def set_safe(self, text: str) -> None:
        with self.lock:
            self.safe = text or ""

    def _refused(self) -> dict[str, Any] | None:
        if self.safe:
            return {"ok": False, "detail": f"modo seguro: los plugins de desarrollo no corren ni se cambian ({self.safe})"}
        return None

    def set_mode(self, on: bool) -> dict[str, Any]:
        with self.lock:
            refused = self._refused()
            if refused:
                return refused
            self.mode = bool(on)
            self._write_state()
            return {"ok": True, "detail": "modo desarrollador " + ("activado" if on else "apagado"), "view": self.snapshot()}

    def approve(self, plugin_id: str, on: bool) -> dict[str, Any]:
        with self.lock:
            refused = self._refused()
            if refused:
                return refused
            p = next((x for x in self.plugins if x["id"] == plugin_id), None)
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin de desarrollo con ese id"}
            if not self.mode:
                return {"ok": False, "detail": "el modo desarrollador está apagado"}
            if on:
                if p["problems"]:
                    return {"ok": False, "detail": "el plugin tiene problemas: " + "; ".join(p["problems"])}
                self.approved[p["id"]] = dev_signature(p)
            else:
                self.approved.pop(plugin_id, None)
            self._write_state()
            return {"ok": True, "detail": "activado" if on else "desactivado", "view": self.snapshot()}

    def _is_approved(self, p: dict[str, Any]) -> bool:
        return not p["problems"] and self.approved.get(p["id"]) == dev_signature(p)

    def _active(self, p: dict[str, Any]) -> bool:
        return self.mode and not self.safe and "page" in p["scopes"] and self._is_approved(p)

    def _preview(self, p: dict[str, Any]) -> str:
        """The start of the code that would run, for the question the user answers: they see what they allow."""
        if "page" not in p.get("files", {}):
            return ""
        try:
            text = Path(p["files"]["page"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[:1200] + (f"\n… ({len(text) - 1200} caracteres más)" if len(text) > 1200 else "")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "mode": self.mode, "forced": False, "folder": str(self.dir), "safeMode": self.safe,
                "plugins": [{
                    "id": p["id"], "name": p["name"], "version": p["version"], "description": p["description"],
                    "scopes": p["scopes"], "problems": p["problems"],
                    "approved": self.mode and not self.safe and self._is_approved(p),
                    "needsReapproval": not p["problems"] and p["id"] in self.approved and not self._is_approved(p),
                    "running": self._active(p),
                    "error": "",
                    "preview": self._preview(p),
                } for p in self.plugins],
            }

    def page_plugins(self) -> list[dict[str, Any]]:
        """The active dev plugins with the page scope, each with the fingerprint of its folder (the page re-imports the
        runner when it changes: that is the hot reload)."""
        with self.lock:
            out = []
            for p in self.plugins:
                if not self._active(p):
                    continue
                try:
                    out.append({"id": p["id"], "name": p["name"], "hash": fingerprint(Path(p["dir"]))})
                except (OSError, ValueError):
                    continue  # it disappeared between the scan and now: the next scan says so
            return out

    def serve_run(self, plugin_id: str) -> tuple[int, str, bytes]:
        """The runner module of an active dev plugin (``/plugin-dev/<id>/run.js``). Its code may change freely (hot
        reload), so there is no 409 here: what is served is read now, and the page re-imports when the fingerprint changes."""
        with self.lock:
            p = next((x for x in self.plugins if x["id"] == plugin_id), None)
            if not p or not self._active(p):
                return 404, "text/plain; charset=utf-8", "este plugin de desarrollo no está activado".encode("utf-8")
            try:
                code = Path(p["files"]["page"]).read_text(encoding="utf-8")
            except (OSError, ValueError):
                return 404, "text/plain; charset=utf-8", b"no se puede leer"
            name = json.dumps(p["id"])
            body = (
                "// Generado por el servidor a partir de la carpeta del plugin de desarrollo.\n"
                f"const run = async (tdt) => {{\n"
                f"  if (!tdt.error) tdt.error = (e) => console.error('[dev:' + {name} + ']', e);\n"
                + code
                + "\n};\n"
                + f"if (window.tdtPluginHost) window.tdtPluginHost.load({name}, run, 'dev');\n"
                + f"else (window.__tdtQueue = window.__tdtQueue || []).push([{name}, run, 'dev']);\n"
            ).encode("utf-8")
            return 200, "text/javascript; charset=utf-8", body
