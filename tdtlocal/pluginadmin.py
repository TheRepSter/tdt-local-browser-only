"""What an AI asks about the plugins through the MCP, in the browser mode (``desktop/lib/plugin-admin.js`` and
``desktop/lib/dev-admin.js``, translated; docs/PARIDAD-NAVEGADOR.md, phase 5). The page forwards the command over
``/api/plugins/admin`` / ``/api/dev/admin``; everything is checked again here, because the page is code too. The commands
that give power answer ``needsConfirm`` until the page says the user accepted (the page asks with the review or the code in
front, one question at a time); only then, with ``confirm=True``, they run. Never raises: always answers a dict.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import pluginwriter

if TYPE_CHECKING:
    from .devplugins import DevStore
    from .plugins import PluginStore


def _log_lines(data_dir: Path, tag: str, limit: int) -> list[str]:
    """The lines of the browser-mode log that belong to this plugin (``<data>/browser/log.txt``, logbook.py)."""
    from .logbook import lines_with_tag  # local: keep the import cheap for the tests that import this module alone
    return lines_with_tag(Path(data_dir) / "browser" / "log.txt", tag, max(1, min(200, limit)))


def run_plugin_admin(store: "PluginStore", cmd: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    """One ``plugin_*`` command. See desktop/lib/plugin-admin.js for the desktop's version of the same rules."""
    from .plugins import full_signature, is_approved, signature  # local: this module is imported by routes, not by plugins

    do = str(cmd.get("do") or "")
    pid = str(cmd.get("id") or "")
    with store.lock:
        if do == "plugin_list":
            return {"ok": True, "detail": "plugins", "data": store.snapshot()}
        if do == "plugin_logs":
            p = store.find(pid)
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin con ese id"}
            limit = cmd.get("limit") if isinstance(cmd.get("limit"), int) else 50
            return {"ok": True, "detail": "registro", "data": {"id": pid,
                    "lines": _log_lines(store.data_dir, f"[plugin:{pid}]", limit) + _log_lines(store.data_dir, f"[plugins] {pid}", 0)[:0]}}
        p = store.find(pid)
        if do in ("plugin_enable", "plugin_disable"):
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin con ese id"}
            on = do == "plugin_enable"
            if on and p["problems"]:
                return {"ok": False, "detail": "el plugin tiene problemas y no se puede activar: " + "; ".join(p["problems"])}
            if on and store.is_on(p):
                return {"ok": True, "detail": "ya estaba activado"}
            if not on and not store.is_on(p):
                return {"ok": True, "detail": "ya estaba desactivado"}
            if on and p["kind"] in ("ui", "full") and not is_approved(p, store.approved):
                if not confirm:
                    review = store.review_of(pid)
                    return {"ok": False, "needsConfirm": True, "detail": "necesita aprobación del usuario",
                            "ask": {"message": f"Activar «{p['name']}»",
                                    "detail": ((review.get("power", "") + "\n" + review.get("asks", "")).strip() if "error" not in review else review["error"]),
                                    "review": review}}
                store.approved[pid] = full_signature(p) if p["kind"] == "full" else signature(p)
                store._write_approved()
            r = store.enable(pid, on)
            return {"ok": r["ok"], "detail": r.get("detail", "activado" if on else "desactivado")}
        if do == "plugin_permission":
            if not p or p["kind"] != "ui":
                return {"ok": False, "detail": "no hay ningún plugin con permisos con ese id"}
            name = str(cmd.get("permission") or "")
            if name not in p.get("permissions", []):
                return {"ok": False, "detail": "el plugin no declara ese permiso"}
            on = cmd.get("on") is True
            granted = name not in store.denied.get(pid, [])
            if granted == on:
                return {"ok": True, "detail": "ya estaba permitido" if on else "ya estaba quitado"}
            if on and not confirm:  # giving it back asks; taking it away does not
                from .plugins import describe_permission
                return {"ok": False, "needsConfirm": True, "detail": "necesita permiso del usuario",
                        "ask": {"message": f"Permitir a «{p['name']}» hacer esto", "detail": describe_permission(name)}}
            r = store.set_permission(pid, name, on)
            return {"ok": r["ok"], "detail": r.get("detail", "permitido" if on else "quitado")}
        if do == "plugin_config":
            if not p or p["kind"] != "ui":
                return {"ok": False, "detail": "no hay ningún plugin con permisos con ese id"}
            values = cmd.get("values")
            if not isinstance(values, dict) or not values:
                return {"ok": False, "detail": "«values» tiene que ser un objeto {ajuste: valor}"}
            if not confirm:
                lines = [f"  • {k}: {str(v)[:200]}" for k, v in values.items()]
                return {"ok": False, "needsConfirm": True, "detail": "necesita permiso del usuario",
                        "ask": {"message": f"Cambiar los ajustes de «{p['name']}»", "detail": "Cambios:\n" + "\n".join(lines)}}
            r = store.set_config(pid, values)
            return {"ok": r["ok"], "detail": r.get("error") or "ajustes guardados"}
        if do == "plugin_read":
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin con ese id"}
            return {"ok": True, "detail": "archivos", "data": {"id": pid, "files": pluginwriter.read_folder(Path(p["dir"]))}}
        if do == "plugin_write":
            return _write(store, p, pid, cmd, confirm, user_root=store.user_dir, tag="plugins")
        return {"ok": False, "detail": "orden desconocida"}


def run_dev_admin(dev: "DevStore", cmd: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    """One ``dev_*`` command. The rules of desktop/lib/dev-admin.js: nothing at all while the developer mode is off, and
    no command can turn it on; a write is checked in a folder apart before any question; activating is a question of its own."""
    do = str(cmd.get("do") or "")
    pid = str(cmd.get("id") or "")
    with dev.lock:
        if not dev.mode:
            return {"ok": False, "detail": "el modo desarrollador está apagado: solo el usuario puede encenderlo (Ajustes ▸ Desarrollo)"}
        if dev.safe:
            return {"ok": False, "detail": f"modo seguro: los plugins de desarrollo no corren ni se cambian ({dev.safe})"}
        if do == "dev_list":
            return {"ok": True, "detail": "plugins de desarrollo", "data": dev.snapshot()}
        if do == "dev_logs":
            limit = cmd.get("limit") if isinstance(cmd.get("limit"), int) else 50
            return {"ok": True, "detail": "registro", "data": {"id": pid, "lines": _log_lines(Path(dev.state_file).parent, f"[dev:{pid}]", limit)}}
        p = next((x for x in dev.plugins if x["id"] == pid), None)
        if do == "dev_activate":
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin de desarrollo con ese id"}
            # clean_command drops «on» from dev_activate: like desktop's activate(id), the command itself means "activate"
            on = cmd.get("on", True) is True
            if on and not confirm:
                return {"ok": False, "needsConfirm": True, "detail": "necesita aprobación del usuario",
                        "ask": {"message": f"Activar «{p['name']}» (sin aislar)",
                                "detail": "Corre dentro de la página de TDT local, sin aislar: puede leer y cambiar cualquier cosa de la página." + ("\n\n" + dev._preview(p) if dev._preview(p) else ""),
                                "review": {"id": pid, "name": p["name"], "version": p["version"], "kind": "dev", "full": True, "theme": False,
                                           "power": "Es un plugin de desarrollo: corre DENTRO de la página, sin aislar.", "preview": dev._preview(p), "asks": "  • ninguno"}}}
            r = dev.approve(pid, on)
            return {"ok": r["ok"], "detail": r.get("detail", "activado" if on else "desactivado")}
        if do == "dev_read":
            folder = dev.dir / pid
            if not folder.is_dir():
                return {"ok": False, "detail": "no hay ningún plugin de desarrollo con ese id"}
            return {"ok": True, "detail": "archivos", "data": {"id": pid, "files": pluginwriter.read_folder(folder)}}
        if do == "dev_write":
            return _write_dev(dev, pid, cmd, confirm)
        return {"ok": False, "detail": "orden desconocida"}


def _write(store: "PluginStore", p: dict[str, Any] | None, pid: str, cmd: dict[str, Any], confirm: bool, user_root: Path, tag: str) -> dict[str, Any]:
    problem = pluginwriter.check_files(cmd.get("files"))
    if problem:
        return {"ok": False, "detail": problem}
    files = cmd.get("files")
    if not isinstance(files, dict):
        return {"ok": False, "detail": "«files» tiene que ser un objeto"}
    if not p:
        base: Path | None = None  # a new plugin in the user's folder
    elif user_root in Path(p["dir"]).parents:
        base = Path(p["dir"])
    else:
        return {"ok": False, "detail": f"solo se pueden escribir los plugins de tu carpeta ({tag})"}
    staged = pluginwriter.stage(files, base, pid)
    try:
        checked = pluginwriter.check_staged(staged)
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)
    if checked["problems"]:
        return {"ok": False, "detail": "el plugin resultante tiene problemas: " + "; ".join(checked["problems"]),
                "data": {"problems": checked["problems"]}}
    if cmd.get("dry_run"):
        return {"ok": True, "detail": "comprobado, sin escribir", "data": {"id": pid, "kind": checked["kind"]}}
    if not confirm:
        existing = pluginwriter.read_folder(base) if base else {}
        changes = pluginwriter.diff_files(existing, files)
        active = bool(p and store.is_on(p))
        ask = pluginwriter.describe_write(p["name"] if p else pid, changes, files, active, p.get("scopes", []) if p else [])
        return {"ok": False, "needsConfirm": True, "detail": "espera la respuesta del usuario", "ask": ask}
    target = base if base else (user_root / pid)
    target.mkdir(parents=True, exist_ok=True)
    pluginwriter.apply_files(files, target)
    store.scan()
    return {"ok": True, "detail": "escrito", "data": {"id": pid}}


def _write_dev(dev: "DevStore", pid: str, cmd: dict[str, Any], confirm: bool) -> dict[str, Any]:
    problem = pluginwriter.check_files(cmd.get("files"))
    if problem:
        return {"ok": False, "detail": problem}
    files = cmd.get("files")
    if not isinstance(files, dict):
        return {"ok": False, "detail": "«files» tiene que ser un objeto"}
    base = dev.dir / pid
    if not base.is_dir():
        base = None
    from .devplugins import read_dev_plugin

    staged = pluginwriter.stage(files, base, pid)
    try:
        plugin = read_dev_plugin(staged)  # a development plugin is read with its own rules (tdt.dev, page scope)
        problems = list(plugin["problems"])
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)
    if problems:
        return {"ok": False, "detail": "el plugin resultante tiene problemas: " + "; ".join(problems),
                "data": {"problems": problems}}
    if cmd.get("dry_run"):
        return {"ok": True, "detail": "comprobado, sin escribir", "data": {"id": pid, "scopes": plugin["scopes"]}}
    if not confirm:
        p = next((x for x in dev.plugins if x["id"] == pid), None)
        existing = pluginwriter.read_folder(base) if base else {}
        changes = pluginwriter.diff_files(existing, files)
        active = bool(p and dev._active(p))
        ask = pluginwriter.describe_write(p["name"] if p else pid, changes, files, active, p.get("scopes", ["page"]) if p else ["page"])
        return {"ok": False, "needsConfirm": True, "detail": "espera la respuesta del usuario", "ask": ask}
    target = base if base else (dev.dir / pid)
    target.mkdir(parents=True, exist_ok=True)
    pluginwriter.apply_files(files, target)
    dev.scan()
    return {"ok": True, "detail": "escrito", "data": {"id": pid}}
