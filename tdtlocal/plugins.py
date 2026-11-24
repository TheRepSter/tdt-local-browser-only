"""The plugin server: the part of the desktop app's plugins that needs no Electron (docs/PARIDAD-NAVEGADOR.md).

A plugin is a folder with a ``manifest.json`` under ``<data>/plugins/`` (the user's), the folders of
``TDT_PLUGINS_DIR``, or the ``tools/`` folders that come with the repository. There are four kinds,
read exactly as ``desktop/lib/plugins.js`` reads them:

- **script** (``content_scripts``, a Chrome-extension manifest): scripts in the pages it declares;
- **with permissions** (``tdt.api: 1``): an HTML page in an isolated frame, with declared permissions;
- **with all the power** (``tdt.full``): code that runs inside the page, approved by the fingerprint
  (SHA-256) of every file of its folder;
- **development** (``tdt.dev``): rejected here (in a normal browser only the ``page`` scope can run,
  and only from its own folder: that is the browser's dev-plugins, phase 4).

This module holds the pure logic (manifest, permissions, packing, catalog, storage) and the
:class:`PluginStore` (discovery, the state the user chose, what is served and what a plugin may ask
of the server: its storage and its ``network:<host>`` requests). The HTTP routes are in
``plugins_routes.py``. Nothing here trusts what a page says: the permission of a call is checked
against what was approved, every time.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import stat as stat_mod
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import pluginsign
from .config import PROJECT_DIR

if TYPE_CHECKING:
    from .config import Config
    from .net import Net

PACKAGE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------------------------------- ids, names, permissions
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # the folder name is the id: lowercase, numbers, hyphens
API_VERSION = 1
MAX_PERMISSIONS = 12
MAX_FILES = 200
PANEL = {"minWidth": 240, "maxWidth": 800, "minHeight": 160, "maxHeight": 800, "width": 420, "height": 320}

PERMISSIONS = {
    "player.read": "ver qué estás viendo: la lista, el canal de cada casilla, el volumen y el modo",
    "player.control": "cambiar lo que ves: poner canales, cambiar de lista, el volumen, el silencio y la multivisión",
    "channels.read": "buscar en la lista de canales y emisoras",
    "epg.read": "leer la guía de programación",
    "ui.toast": "mostrar avisos breves sobre la imagen",
    "storage": "guardar sus propios datos en tu equipo (hasta 256 KB)",
    "provide:epg": "poner programas en la guía de los canales que ella misma aporta (títulos y horas que luego ves en la lista)",
    "provide:channels": "añadir canales a tu lista (hasta 500): los enlaces que traiga se podrán abrir desde tu equipo, como los que añades tú",
}
RISK = {
    "player.read": "medium",
    "player.control": "high",
    "channels.read": "medium",
    "epg.read": "medium",
    "ui.toast": "low",
    "storage": "low",
    "provide:channels": "high",
    "provide:epg": "medium",
}
RISK_TEXT = {"low": "bajo", "medium": "medio", "high": "alto", "critical": "crítico"}

HOST = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
NOT_PUBLIC_NAME = re.compile(r"(^|\.)(localhost|local|internal|lan|home|corp|intranet)$")


def risk_of(permission: str) -> str:
    if permission.startswith("embed:"):
        return "critical"
    return "high" if permission.startswith("network:") else RISK.get(permission, "high")


def parse_permission(raw: Any) -> dict[str, Any]:
    """One permission as the manifest writes it: ``{name}``, ``{network, host, wildcard, name}``, ``{embed, host, name}``
    or ``{problem}``."""
    if not isinstance(raw, str):
        return {"problem": "un permiso tiene que ser texto"}
    if raw in PERMISSIONS:
        return {"name": raw}
    if raw.startswith("network:"):
        host = raw[8:]
        if not HOST.match(host) or len(host) > 120 or NOT_PUBLIC_NAME.search(host):
            return {"problem": f"«{raw[:80]}»: el servidor tiene que ser un nombre público como api.ejemplo.com o *.ejemplo.com"}
        return {"network": True, "host": host.removeprefix("*."), "wildcard": host.startswith("*."), "name": raw}
    if raw.startswith("embed:"):
        host = raw[6:]
        if not HOST.match(host) or host.startswith("*.") or len(host) > 120 or NOT_PUBLIC_NAME.search(host):
            return {"problem": f"«{raw[:80]}»: el servidor tiene que ser un nombre público como player.ejemplo.tv (sin comodines)"}
        return {"embed": True, "host": host, "name": raw}
    return {"problem": f"permiso desconocido: «{str(raw)[:60]}»"}


def describe_permission(permission: str | dict[str, Any]) -> str:
    p = parse_permission(permission) if isinstance(permission, str) else permission
    if "problem" in p:
        return p["problem"]
    if p.get("embed"):
        return f"mostrar dentro de una casilla el reproductor de {p['host']}: una página suya, con sus propios scripts, corre en tu ventana"
    if p.get("network"):
        return f"hablar con cualquier subdominio de {p['host']} (solo por https)" if p["wildcard"] else f"hablar con {p['host']} (solo por https)"
    return PERMISSIONS[p["name"]]


def network_allows(permissions: list[str], host: str) -> bool:
    """Does one of the ``network:`` permissions cover this host?"""
    for raw in permissions:
        p = parse_permission(raw)
        if p.get("network"):
            if host.endswith(f".{p['host']}") if p["wildcard"] else host == p["host"]:
                return True
    return False


# ---------------------------------------------------------------------------------------------------- content_scripts patterns
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
LOCAL_HOST = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|0\.0\.0\.0)$", re.I)


def parse_pattern(pattern: Any) -> dict[str, Any]:
    """A Chrome-style match pattern as a test on addresses, or ``{problem}`` for the ones a plugin must not have."""
    m = re.match(r"^(https?|\*)://([^/]+)(/.*)$", str(pattern))
    if not m:
        return {"problem": f"patrón no válido: «{pattern}» (usa https://sitio/ruta/*)"}
    scheme, host, path = m.groups()
    if host == "*" or LOCAL_HOST.match(re.sub(r":\d+$", "", host)):
        return {"problem": f"patrón demasiado amplio o local: «{pattern}»"}
    if re.search(r"[^\w.\-*:[\]]", host) or ("*" in host and not re.match(r"^\*\.[^*]+$", host)):
        return {"problem": f"patrón no válido: «{pattern}»"}
    host_re = (r"([^/]+\.)?" + re.escape(host[2:])) if host.startswith("*.") else re.escape(host)
    path_re = ".*".join(re.escape(part) for part in path.split("*"))
    rx = re.compile(f"^{'https?' if scheme == '*' else scheme}://{host_re}{path_re}$", re.I)

    def test(url: str) -> bool:
        try:
            u = urllib.parse.urlsplit(url)
            return bool(rx.match(f"{u.scheme}://{u.netloc}{u.path}{u.query}"))
        except ValueError:
            return False

    return {"test": test}


# ---------------------------------------------------------------------------------------------------- files inside a folder
def inside_of(dir_path: Path, file: Any) -> Path | None:
    """Is this file path inside the plugin's folder (no ``..``, no absolute path, no link that leaves it)?"""
    if not isinstance(file, str) or not file or "\0" in file:
        return None
    try:
        full = (Path(dir_path) / file).resolve()
        if not full.exists():
            return None
        real = Path(os.path.realpath(full))
        base = Path(os.path.realpath(dir_path))
        rel = os.path.relpath(real, base)
    except (OSError, ValueError):
        return None
    if not rel or rel == ".." or rel.startswith(f"..{os.sep}") or os.path.isabs(rel):
        return None
    if (base / rel).resolve() != real:
        return None
    return real


def files_of(dir_path: Path, prefix: str = "") -> list[str]:
    """The files of a folder as relative paths with ``/``, in order (hidden ones, node_modules and packages left out).
    A link is an error."""
    out: list[str] = []
    base = Path(dir_path) / prefix if prefix else Path(dir_path)
    for entry in sorted(os.listdir(base)):
        if entry.startswith(".") or entry == "node_modules" or entry.endswith(".tdtplugin"):
            continue
        rel = f"{prefix}/{entry}" if prefix else entry
        full = base / entry
        st = os.lstat(full)
        if stat_mod.S_ISLNK(st.st_mode):
            raise ValueError(f"«{rel}» es un enlace: un plugin no puede llevar enlaces")
        if stat_mod.S_ISDIR(st.st_mode):
            out.extend(files_of(dir_path, rel))
        elif stat_mod.S_ISREG(st.st_mode):
            out.append(rel)
        if len(out) > MAX_FILES:
            raise ValueError(f"demasiados archivos (hasta {MAX_FILES})")
    return out


def counted(path: Any) -> bool:
    """Does the fingerprint count this file? Hidden files, node_modules and packages do not count, and neither may they run."""
    parts = str(path).replace("\\", "/").split("/")
    parts = [p for p in parts if p not in ("", ".")]
    return not any((p.startswith(".") and p != "..") or p == "node_modules" for p in parts) and not parts[-1].endswith(".tdtplugin")


def fingerprint(dir_path: Path) -> str:
    """A fingerprint of the code: the names and the contents of every file of the folder."""
    lines = [f"{rel}\0{hashlib.sha256((Path(dir_path) / rel).read_bytes()).hexdigest()}" for rel in files_of(dir_path)]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


MAX_CSS = 1024 * 1024
MAX_ASSET = 1024 * 1024
MAX_ASSETS = 4 * 1024 * 1024
ASSET_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf",
}
_URL_RE = re.compile(r"(/\*[\s\S]*?\*/)|url\(\s*(['\"]?)([^'\")]*)\2\s*\)", re.I)


def inline_assets(css: str, dir_path: Path) -> dict[str, Any]:
    """Put the pictures and fonts of a style sheet inside it (``data:``), from files of the plugin's own folder only."""
    problems: list[str] = []
    if re.search(r"@import", re.sub(r"/\*[\s\S]*?\*/", "", css), re.I):
        problems.append("la hoja de estilo usa «@import»: junta las reglas en un solo archivo")
    state = {"total": 0}
    cache: dict[str, str] = {}

    def replace(m: re.Match[str]) -> str:
        whole, comment, address = m.group(1), m.group(2), m.group(4)
        if comment:
            return whole
        target = address.strip()
        if not target or target.startswith("#") or target.lower().startswith("data:"):
            return whole
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("//"):
            problems.append(f"«{target[:60]}»: los recursos tienen que estar en la carpeta del plugin, no en otra dirección")
            return whole
        clean = target.split("?")[0].split("#")[0]
        if clean in cache:
            return f'url("{cache[clean]}")'
        if not counted(clean):
            problems.append(f"«{clean[:60]}» no cuenta en la huella del código (es un archivo oculto o está en node_modules): ponlo en otra ruta")
            return whole
        ext = os.path.splitext(clean)[1].lower()
        kind = ASSET_TYPES.get(ext)
        real = inside_of(dir_path, clean.lstrip("./")) if kind else None
        if not kind:
            problems.append(f"«{clean[:60]}»: tipo de archivo no permitido (hay imágenes y fuentes)")
        elif not real:
            problems.append(f"«{clean[:60]}» no existe o está fuera de la carpeta")
        else:
            data = real.read_bytes()
            state["total"] += len(data)
            if len(data) > MAX_ASSET:
                problems.append(f"«{clean[:60]}» pesa más de {MAX_ASSET // 1048576} MB")
            elif state["total"] > MAX_ASSETS:
                problems.append(f"los recursos pesan más de {MAX_ASSETS // 1048576} MB")
            else:
                uri = f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"
                cache[clean] = uri
                return f'url("{uri}")'
        return whole

    return {"css": _URL_RE.sub(replace, css), "problems": problems}


# ---------------------------------------------------------------------------------------------------- the manifest, kind by kind
def _read_manifest(dir_path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        manifest = json.loads((Path(dir_path) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"manifest.json no se puede leer: {exc}"
    if not isinstance(manifest, dict):
        return None, "manifest.json no es un objeto"
    return manifest, ""


def read_plugin(dir_path: Path) -> dict[str, Any]:
    """Read one folder as a plugin. Always returns a dict with ``id``, ``dir`` and ``problems``; one with problems is
    listed (so the user sees why) but never runs."""
    dir_path = Path(dir_path)
    plugin_id = dir_path.name
    plugin: dict[str, Any] = {
        "id": plugin_id, "dir": str(dir_path), "kind": "script", "name": plugin_id, "version": "",
        "description": "", "source": "", "sites": [], "rules": [], "problems": [],
    }
    manifest, problem = _read_manifest(dir_path)
    if manifest is None:
        plugin["problems"].append(problem)
        return plugin
    plugin["name"] = str(manifest.get("name") or plugin_id)[:120]
    plugin["version"] = str(manifest.get("version") or "")
    plugin["description"] = str(manifest.get("description") or "")[:400]
    tdt = manifest.get("tdt")
    if isinstance(tdt, dict) and "full" in tdt:
        return read_full_plugin(manifest, dir_path, plugin)
    if isinstance(tdt, dict) and "api" in tdt and "dev" not in tdt:
        return read_ui_plugin(manifest, dir_path, plugin)
    if isinstance(tdt, dict) and "dev" in tdt:
        plugin["problems"].append("es un plugin de desarrollo: solo se carga desde la carpeta de desarrollo, con el modo desarrollador")
    for key in ("permissions", "host_permissions", "optional_permissions", "background", "externally_connectable"):
        value = manifest.get(key)
        if value is not None and not (isinstance(value, list) and not value):
            plugin["problems"].append(f"pide «{key}», que esta versión no ofrece a los plugins")
    scripts = manifest.get("content_scripts")
    scripts = scripts if isinstance(scripts, list) else []
    if not scripts:
        plugin["problems"].append("no declara content_scripts")
    for entry in scripts:
        matches = entry.get("matches") if isinstance(entry, dict) and isinstance(entry.get("matches"), list) else []
        patterns = [parse_pattern(p) for p in matches]
        for p in patterns:
            if "problem" in p:
                plugin["problems"].append(p["problem"])
        if not patterns:
            plugin["problems"].append("un content_script no dice en qué páginas actúa (matches)")
        files = []
        js_files = entry.get("js") if isinstance(entry, dict) and isinstance(entry.get("js"), list) else []
        for file in js_files:
            real = inside_of(dir_path, file)
            if not real:
                plugin["problems"].append(f"el script «{file}» no existe o está fuera de la carpeta")
            elif real.stat().st_size > MAX_SCRIPT_BYTES:
                plugin["problems"].append(f"el script «{file}» es demasiado grande")
            else:
                files.append({"file": file, "real": str(real)})
        if not files:
            plugin["problems"].append("un content_script no tiene scripts (js)")
        plugin["rules"].append({
            "patterns": [p for p in patterns if "test" in p],
            "allFrames": bool(isinstance(entry, dict) and entry.get("all_frames") is True),
            "files": files,
        })
        if isinstance(entry, dict) and isinstance(entry.get("matches"), list):
            for pattern in entry["matches"]:
                text = str(pattern)
                if text not in plugin["sites"]:
                    plugin["sites"].append(text)
    return plugin


MAX_THEMES = 5
MAX_BROUGHT_RULES = 10
MAX_SOURCES = 5
CONTROLS = {"youtube": ["www.youtube-nocookie.com", "www.youtube.com"], "atres": ["www.atresplayer.com"]}
MAX_ACTIVATION = 8
ACTIVATION = re.compile(r"^(onStartup|onEvent:[a-z]+(\.[a-z]+)?|onTime:([01]\d|2[0-3]):[0-5]\d)$")


def read_themes(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"list": [], "problems": []}
    if (not isinstance(raw, list) or len(raw) > MAX_THEMES
            or any(not isinstance(t, dict) or not isinstance(t.get("id"), str) or not isinstance(t.get("name"), str)
                   or not isinstance(t.get("vars"), dict) for t in raw)):
        return {"list": [], "problems": [f'«tdt.themes» es una lista de hasta {MAX_THEMES} temas {{id, name, vars}}']}
    return {"list": raw, "problems": []}


def read_brought_rules(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"list": [], "problems": []}
    if not isinstance(raw, list) or len(raw) > MAX_BROUGHT_RULES or any(not isinstance(r, dict) for r in raw):
        return {"list": [], "problems": [f'«tdt.rules» es una lista de hasta {MAX_BROUGHT_RULES} reglas (objetos)']}
    return {"list": raw, "problems": []}


def read_sources(raw: Any, permissions: list[str]) -> dict[str, Any]:
    if raw is None:
        return {"list": [], "problems": []}
    if not isinstance(raw, list) or len(raw) > MAX_SOURCES:
        return {"list": [], "problems": [f'«tdt.sources» es una lista de hasta {MAX_SOURCES} fuentes']}
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    allowed = {parse_permission(p)["host"] for p in (permissions or []) if parse_permission(p).get("embed")}
    for i, s in enumerate(raw):
        at = f"fuente {i + 1}"
        if not isinstance(s, dict):
            problems.append(f"{at}: tiene que ser {{name, match, embed}}")
            continue
        name = str(s.get("name") or "").strip()[:40]
        match_raw, embed_raw = str(s.get("match", "")), str(s.get("embed", ""))
        try:
            match = urllib.parse.urlsplit(re.sub(r"\{[1-3]\}", "x", match_raw))
            embed = urllib.parse.urlsplit(re.sub(r"\{[1-3]\}", "x", embed_raw).replace("{parent}", "x"))
        except ValueError:
            problems.append(f"{at}: «match» y «embed» tienen que ser direcciones https")
            continue
        if not name:
            problems.append(f"{at}: falta «name»")
        elif match.scheme != "https" or embed.scheme != "https":
            problems.append(f"{at}: solo https")
        elif len(match_raw) > 200 or len(embed_raw) > 400:
            problems.append(f"{at}: direcciones demasiado largas")
        elif re.search(r"[{}]", re.sub(r"\{[1-3]\}", "", match_raw)) or re.search(r"[{}]", re.sub(r"\{[1-3]\}", "", embed_raw).replace("{parent}", "")) \
                or re.match(r"^https://[^/?#]*[{}]", match_raw) or re.match(r"^https://[^/?#]*[{}]", embed_raw):
            problems.append(f"{at}: los huecos son {{1}}, {{2}}, {{3}} (y {{parent}} en «embed»), y nunca en el servidor")
        elif "{parent}" in embed_raw.split("?")[0]:
            problems.append(f"{at}: {{parent}} solo va después de «?»")
        elif embed.hostname not in allowed:
            problems.append(f"{at}: pide «embed:{embed.hostname}» en tdt.permissions para poder mostrar su reproductor")
        elif s.get("control") is not None and s["control"] not in CONTROLS:
            problems.append(f"{at}: «control» es {' o '.join(CONTROLS)}")
        elif s.get("control") is not None and embed.hostname not in CONTROLS[s["control"]]:
            problems.append(f"{at}: el control «{s['control']}» solo vale con {' o '.join(CONTROLS[s['control']])}")
        else:
            item = {"name": name, "match": match_raw, "embed": embed_raw}
            if s.get("control") is not None:
                item["control"] = s["control"]
            out.append(item)
    return {"list": out, "problems": problems}


def read_activation(raw: Any, has_panel: bool) -> dict[str, Any]:
    if raw is None:
        return {"list": [], "problems": []}
    if not isinstance(raw, list) or len(raw) > MAX_ACTIVATION:
        return {"list": [], "problems": [f'«tdt.activation» es una lista de hasta {MAX_ACTIVATION} entradas']}
    bad = next((a for a in raw if not isinstance(a, str) or not ACTIVATION.match(a)), None)
    if bad is not None:
        return {"list": [], "problems": [f'«tdt.activation»: «{str(bad)[:40]}» no vale; hay onStartup, onEvent:evento (channel.changed…) y onTime:HH:MM']}
    if raw and has_panel:
        return {"list": [], "problems": ['«tdt.activation» solo vale para plugins sin panel: un panel se ve siempre']}
    return {"list": list(dict.fromkeys(raw)), "problems": []}


def read_ui_plugin(manifest: dict[str, Any], dir_path: Path, plugin: dict[str, Any]) -> dict[str, Any]:
    """A plugin with permissions (``tdt.api: 1``): manifest, panel, permissions, settings, sources, themes, rules."""
    tdt = manifest["tdt"]
    plugin.update({
        "kind": "ui", "api": tdt.get("api"), "author": str(manifest.get("author") or "")[:120],
        "homepage": str(manifest["homepage"])[:200] if re.match(r"^https://", str(manifest.get("homepage") or "")) else "",
        "label": str(tdt.get("label") or plugin["name"])[:24], "entry": "", "panel": None,
        "permissions": [], "activation": [], "sources": [], "themes": [], "rules": [], "settings": {},
    })
    if tdt.get("api") != API_VERSION:
        plugin["problems"].append(f'«tdt.api» tiene que ser {API_VERSION}')
    if not ID.match(plugin["id"]):
        plugin["problems"].append("el nombre de la carpeta (el id) solo puede llevar minúsculas, números y guiones, y empezar por una letra o un número")
    if isinstance(manifest.get("content_scripts"), list) and manifest["content_scripts"]:
        plugin["problems"].append('un plugin es de un tipo o de otro: no puede llevar «tdt.api» y «content_scripts»')
    for key in ("permissions", "host_permissions", "optional_permissions", "background", "externally_connectable"):
        if manifest.get(key) is not None and not (isinstance(manifest[key], list) and not manifest[key]):
            plugin["problems"].append(f'«{key}» no se usa: los permisos van en «tdt.permissions»')
    entry = "index.html" if tdt.get("entry") is None else tdt["entry"]
    if not isinstance(entry, str) or not re.search(r"\.html?$", entry, re.I) or not inside_of(dir_path, entry):
        plugin["problems"].append(f'la página «{str(entry)[:60]}» (tdt.entry) no existe, está fuera de la carpeta o no es .html')
    else:
        plugin["entry"] = entry
    if tdt.get("panel") is not None and tdt["panel"] is not False:
        size = {} if tdt["panel"] is True else tdt["panel"]
        placement = "float" if not isinstance(size, dict) or size.get("placement") is None else size.get("placement")
        if not isinstance(size, dict):
            plugin["problems"].append('«tdt.panel» tiene que ser true o {width, height, placement}')
        elif placement not in ("float", "screen"):
            plugin["problems"].append('«tdt.panel.placement» tiene que ser "float" o "screen"')
        else:
            def clamp(v: Any, lo: int, hi: int, d: int) -> int:
                return max(lo, min(hi, round(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
            plugin["panel"] = {
                "width": clamp(size.get("width"), PANEL["minWidth"], PANEL["maxWidth"], PANEL["width"]),
                "height": clamp(size.get("height"), PANEL["minHeight"], PANEL["maxHeight"], PANEL["height"]),
                "placement": placement,
            }
    activation = read_activation(tdt.get("activation"), bool(plugin["panel"]))
    plugin["activation"] = activation["list"]
    plugin["problems"].extend(activation["problems"])
    declared = read_settings(tdt.get("settings"))
    plugin["settings"] = declared["fields"]
    plugin["problems"].extend(declared["problems"])
    asked = [] if tdt.get("permissions") is None else tdt["permissions"]
    if not isinstance(asked, list):
        plugin["problems"].append('«tdt.permissions» tiene que ser una lista')
    elif len(asked) > MAX_PERMISSIONS:
        plugin["problems"].append(f"demasiados permisos (hasta {MAX_PERMISSIONS})")
    else:
        for raw in asked:
            p = parse_permission(raw)
            if "problem" in p:
                plugin["problems"].append(p["problem"])
            elif p["name"] not in plugin["permissions"]:
                plugin["permissions"].append(p["name"])
    sources = read_sources(tdt.get("sources"), plugin["permissions"])
    plugin["sources"] = sources["list"]
    plugin["problems"].extend(sources["problems"])
    themes = read_themes(tdt.get("themes"))
    plugin["themes"] = themes["list"]
    plugin["problems"].extend(themes["problems"])
    brought = read_brought_rules(tdt.get("rules"))
    plugin["rules"] = brought["list"]
    plugin["problems"].extend(brought["problems"])
    return plugin


SCOPES = ["page", "css"]


def read_full_plugin(manifest: dict[str, Any], dir_path: Path, plugin: dict[str, Any]) -> dict[str, Any]:
    """A plugin with all the power (``tdt.full``): scopes ``page`` and ``css``, approved by the fingerprint of its files."""
    tdt = manifest["tdt"]
    plugin.update({
        "kind": "full", "api": tdt.get("api"), "author": str(manifest.get("author") or "")[:120],
        "homepage": str(manifest["homepage"])[:200] if re.match(r"^https://", str(manifest.get("homepage") or "")) else "",
        "scopes": [], "files": {}, "permissions": [], "hash": "",
    })
    if tdt.get("api") != 1:
        plugin["problems"].append('«tdt.api» tiene que ser 1')
    if not ID.match(plugin["id"]):
        plugin["problems"].append("el nombre de la carpeta (el id) solo puede llevar minúsculas, números y guiones, y empezar por una letra o un número")
    if "dev" in tdt:
        plugin["problems"].append('un plugin es de desarrollo («tdt.dev») o con todo el poder («tdt.full»), no las dos cosas')
    if isinstance(manifest.get("content_scripts"), list) and manifest["content_scripts"]:
        plugin["problems"].append('un plugin es de un tipo o de otro: no puede llevar «tdt.full» y «content_scripts»')
    for key in ("permissions", "entry", "panel", "settings", "sources", "themes", "rules", "activation"):
        if key in tdt:
            plugin["problems"].append(f'«tdt.{key}» no se usa en un plugin con todo el poder: no pide permisos y se ocupa él mismo de lo que muestra')
    full = tdt.get("full")
    if not isinstance(full, dict):
        plugin["problems"].append('«tdt.full» tiene que ser un objeto, por ejemplo {"page": "page.js"} o {"css": "tema.css"}')
        return plugin
    for key in full:
        if key not in SCOPES:
            if key in ("main", "server"):
                plugin["problems"].append(f"el alcance «{key}» no está disponible en un navegador: solo existe en la aplicación de escritorio (en plugins de desarrollo)")
            else:
                plugin["problems"].append(f"el alcance «{key}» no existe (hay: {', '.join(SCOPES)})")
    if full.get("page") is None and full.get("css") is None:
        if not plugin["problems"]:
            plugin["problems"].append('«tdt.full» no declara ningún alcance (por ejemplo «page» o «css»)')
        return plugin
    if full.get("page") is not None:
        page = full["page"]
        real = inside_of(dir_path, page) if isinstance(page, str) and counted(page) else None
        if isinstance(page, str) and not counted(page):
            plugin["problems"].append(f'el archivo «{str(page)[:60]}» no cuenta en la huella del código (es un archivo oculto o está en node_modules): ponlo en otra ruta')
        elif not real:
            plugin["problems"].append(f'el archivo «{str(page)[:60]}» del alcance «page» no existe o está fuera de la carpeta')
        elif real.stat().st_size > MAX_SCRIPT_BYTES:
            plugin["problems"].append(f'el archivo «{page}» es demasiado grande')
        else:
            plugin["scopes"].append("page")
            plugin["files"]["page"] = str(real)
    if full.get("css") is not None:
        css = full["css"]
        real = inside_of(dir_path, css) if isinstance(css, str) and re.search(r"\.css$", css, re.I) and counted(css) else None
        if isinstance(css, str) and re.search(r"\.css$", css, re.I) and not counted(css):
            plugin["problems"].append(f'el archivo «{str(css)[:60]}» no cuenta en la huella del código (es un archivo oculto o está en node_modules): ponlo en otra ruta')
        elif not real:
            plugin["problems"].append(f'el archivo «{str(css)[:60]}» del alcance «css» no existe, está fuera de la carpeta o no es .css')
        elif real.stat().st_size > MAX_CSS:
            plugin["problems"].append(f'el archivo «{css}» pesa más de {MAX_CSS // 1048576} MB')
        else:
            checked = inline_assets(real.read_text(encoding="utf-8"), dir_path)
            if checked["problems"]:
                plugin["problems"].extend(f'«{css}»: {p}' for p in checked["problems"])
            else:
                plugin["scopes"].append("css")
                plugin["files"]["css"] = str(real)
    if not plugin["problems"]:
        try:
            plugin["hash"] = fingerprint(dir_path)
        except (OSError, ValueError) as exc:
            plugin["problems"].append(str(exc))
    return plugin


def discover(roots: list[Any]) -> list[dict[str, Any]]:
    """Every plugin found in the given folders (each a folder with a ``manifest.json``). The first folder wins on a
    repeated id. A folder can be a path or ``{dir, label}``."""
    found: dict[str, dict[str, Any]] = {}
    for entry in roots:
        root = Path(entry["dir"] if isinstance(entry, dict) else entry)
        label = entry.get("label", "") if isinstance(entry, dict) else ""
        try:
            names = sorted(d.name for d in os.scandir(root) if d.is_dir())
        except OSError:
            continue
        for name in names:
            folder = root / name
            if (folder / "manifest.json").exists() and name not in found:
                found[name] = {**read_plugin(folder), "source": label}
    return list(found.values())


# ---- what the user approves
def signature(plugin: dict[str, Any]) -> str:
    """What the user approves for a plugin with permissions: id, api, the exact permissions and the panel."""
    panel = plugin.get("panel")
    return f"{plugin['id']}|{plugin.get('api')}|{','.join(sorted(plugin.get('permissions', [])))}|{'pantalla' if panel and panel.get('placement') == 'screen' else 'panel' if panel else 'sin-panel'}"


def full_signature(plugin: dict[str, Any]) -> str:
    return f"{plugin['id']}|full|{','.join(sorted(plugin.get('scopes', [])))}|{plugin.get('hash', '')}"


def is_approved(plugin: dict[str, Any], approved: dict[str, str]) -> bool:
    if plugin.get("problems"):
        return False
    want = full_signature(plugin) if plugin.get("kind") == "full" else signature(plugin) if plugin.get("kind") == "ui" else None
    return want is not None and approved.get(plugin["id"]) == want


def needs_reapproval(plugin: dict[str, Any], approved: dict[str, str]) -> bool:
    if plugin.get("problems") or plugin.get("kind") not in ("ui", "full"):
        return False
    want = full_signature(plugin) if plugin["kind"] == "full" else signature(plugin)
    return plugin["id"] in approved and approved[plugin["id"]] != want


def effective_permissions(plugin: dict[str, Any], denied: dict[str, list[str]]) -> list[str]:
    off = denied.get(plugin["id"], [])
    return [p for p in plugin.get("permissions", []) if p not in off]


# ---------------------------------------------------------------------------------------------------- plugin settings (tdt.settings)
MAX_FIELDS = 20
MAX_OPTIONS = 20
MAX_TEXT = 500
FIELD = re.compile(r"^[a-z][A-Za-z0-9_]{0,31}$")
TYPES = ["string", "number", "boolean", "choice"]


def _plain(v: Any) -> bool:
    return isinstance(v, dict)


def clean_value(name: str, field: dict[str, Any], value: Any) -> dict[str, Any]:
    kind = field["type"]
    if kind == "boolean":
        return {"ok": True, "value": value} if isinstance(value, bool) else {"ok": False, "error": f'«{name}» tiene que ser true o false'}
    if kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
            return {"ok": False, "error": f'«{name}» tiene que ser un número'}
        if value < field["min"] or value > field["max"]:
            return {"ok": False, "error": f'«{name}» tiene que estar entre {field["min"]} y {field["max"]}'}
        return {"ok": True, "value": value}
    if kind == "choice":
        return {"ok": True, "value": value} if value in field["options"] else {"ok": False, "error": f'«{name}» tiene que ser uno de: {", ".join(field["options"])}'}
    if not isinstance(value, str):
        return {"ok": False, "error": f'«{name}» tiene que ser un texto'}
    if len(value) > field["maxLength"]:
        return {"ok": False, "error": f'«{name}» pasa de {field["maxLength"]} caracteres'}
    if re.search(r"[\x00-\x1f]", value):
        return {"ok": False, "error": f'«{name}» no puede llevar saltos de línea ni caracteres de control'}
    return {"ok": True, "value": value}


def read_settings(raw: Any) -> dict[str, Any]:
    """The schema a manifest declares, checked: ``{fields, problems}``."""
    fields: dict[str, Any] = {}
    problems: list[str] = []
    if raw is None:
        return {"fields": fields, "problems": problems}
    if not _plain(raw):
        return {"fields": fields, "problems": ["«tdt.settings» tiene que ser un objeto {nombre: campo}"]}
    names = list(raw.keys())
    if len(names) > MAX_FIELDS:
        problems.append(f'«tdt.settings» tiene demasiados campos (hasta {MAX_FIELDS})')
    for name in names[:MAX_FIELDS]:
        f = raw[name]
        at = f"ajuste «{str(name)[:40]}»"
        if not FIELD.match(name):
            problems.append(f"{at}: el nombre empieza por una letra minúscula y lleva letras, números o _ (hasta 32)")
            continue
        if not _plain(f) or f.get("type") not in TYPES:
            problems.append(f"{at}: «type» tiene que ser {', '.join(TYPES)}")
            continue
        field: dict[str, Any] = {
            "type": f["type"],
            "label": (f["label"].strip()[:80] if isinstance(f.get("label"), str) and f["label"].strip() else name),
            "help": f["help"].strip()[:200] if isinstance(f.get("help"), str) else "",
        }
        if f["type"] == "number":
            field["min"] = f["min"] if isinstance(f.get("min"), (int, float)) and not isinstance(f.get("min"), bool) else 0
            field["max"] = f["max"] if isinstance(f.get("max"), (int, float)) and not isinstance(f.get("max"), bool) else 1_000_000
            if field["min"] > field["max"]:
                problems.append(f"{at}: «min» es mayor que «max»")
                continue
        elif f["type"] == "choice":
            options = f.get("options")
            if (not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS
                    or any(not isinstance(o, str) or not o or len(o) > 60 for o in options)
                    or len(set(options)) != len(options)):
                problems.append(f"{at}: «options» son de 2 a {MAX_OPTIONS} textos distintos de hasta 60 caracteres")
                continue
            field["options"] = list(options)
        elif f["type"] == "string":
            field["maxLength"] = f["maxLength"] if isinstance(f.get("maxLength"), int) and 1 <= f["maxLength"] <= MAX_TEXT else 200
        fallback = {"boolean": False, "number": field.get("min"), "choice": field["options"][0] if field.get("options") else None, "string": ""}[f["type"]]
        given = {"ok": True, "value": fallback} if f.get("default") is None else clean_value(name, field, f["default"])
        if not given["ok"]:
            problems.append(f"{at}: el valor por defecto no vale ({given['error']})")
            continue
        field["default"] = given["value"]
        fields[name] = field
    return {"fields": fields, "problems": problems}


def values_of(fields: dict[str, Any], stored: Any) -> dict[str, Any]:
    out = {}
    for name, field in (fields or {}).items():
        have = clean_value(name, field, stored[name]) if isinstance(stored, dict) and name in stored else {"ok": False}
        out[name] = have["value"] if have["ok"] else field["default"]
    return out


def apply_changes(fields: dict[str, Any], stored: Any, changes: Any) -> dict[str, Any]:
    if not _plain(changes) or not changes:
        return {"ok": False, "error": "no hay nada que cambiar: {ajuste: valor}"}
    nxt = dict(values_of(fields, stored))
    for name, value in changes.items():
        if name not in (fields or {}):
            return {"ok": False, "error": f'el plugin no tiene ningún ajuste «{str(name)[:40]}»'}
        checked = clean_value(name, fields[name], value)
        if not checked["ok"]:
            return checked
        nxt[name] = checked["value"]
    return {"ok": True, "values": nxt}


def form_of(fields: dict[str, Any], stored: Any) -> list[dict[str, Any]]:
    values = values_of(fields, stored)
    return [{"name": name, **field, "value": values[name]} for name, field in (fields or {}).items()]


class ConfigStore:
    """The values the user chose for each plugin, in a file of the data folder."""

    def __init__(self, file: Path) -> None:
        self.file = Path(file)
        self._data: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        try:
            parsed = json.loads(self.file.read_text(encoding="utf-8"))
            self._data = parsed if _plain(parsed) else {}
        except (OSError, ValueError):
            self._data = {}
        return self._data

    def get(self, plugin_id: str) -> dict[str, Any]:
        with self._lock:
            value = self._load().get(plugin_id)
            return value if _plain(value) else {}

    def set(self, plugin_id: str, values: dict[str, Any]) -> None:
        with self._lock:
            self._load()[plugin_id] = values
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(self.file.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            os.replace(tmp, self.file)

    def remove(self, plugin_id: str) -> None:
        with self._lock:
            if plugin_id in self._load():
                del self._data[plugin_id]
                self.file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.file.with_suffix(self.file.suffix + ".tmp")
                tmp.write_text(json.dumps(self._data), encoding="utf-8")
                os.replace(tmp, self.file)


# ---------------------------------------------------------------------------------------------------- storage per plugin
STORAGE_KEY = re.compile(r"^[\w.-]{1,64}$", re.ASCII)
STORAGE = {"value": 65_536, "total": 262_144, "keys": 200}


class PluginStorage:
    """A JSON file per plugin in ``dir``, with limits; writes are cached and flushed a moment later."""

    def __init__(self, dir_path: Path, delay_ms: int = 500) -> None:
        self.dir = Path(dir_path)
        self.delay_ms = delay_ms
        self._cache: dict[str, dict[str, Any]] = {}
        self._dirty: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def _file(self, plugin_id: str) -> Path:
        return self.dir / f"{plugin_id}.json"

    def _load(self, plugin_id: str) -> dict[str, Any]:
        if plugin_id in self._cache:
            return self._cache[plugin_id]
        data: dict[str, Any] = {}
        try:
            parsed = json.loads(self._file(plugin_id).read_text(encoding="utf-8"))
            if _plain(parsed):
                data = parsed
        except (OSError, ValueError):
            pass
        self._cache[plugin_id] = data
        return data

    def _changed(self, plugin_id: str) -> None:
        self._dirty.add(plugin_id)
        if self.delay_ms <= 0:
            self.flush()
        elif self._timer is None:
            self._timer = threading.Timer(self.delay_ms / 1000, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            dirty, self._dirty = self._dirty, set()
        for plugin_id in dirty:
            try:
                with self._lock:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    tmp = self._file(plugin_id).with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(self._cache.get(plugin_id, {})), encoding="utf-8")
                    os.replace(tmp, self._file(plugin_id))
            except OSError:
                pass  # a full disk must not stop the app

    def get(self, plugin_id: str, key: Any) -> Any:
        with self._lock:
            if not STORAGE_KEY.match(str(key)):
                return None
            return self._load(plugin_id).get(str(key))

    def keys(self, plugin_id: str) -> list[str]:
        with self._lock:
            return sorted(self._load(plugin_id).keys())

    def set(self, plugin_id: str, key: Any, value: Any) -> bool:
        if not STORAGE_KEY.match(str(key)):
            raise ValueError("la clave puede llevar letras, números, punto, guion y guion bajo (hasta 64)")
        try:
            text = json.dumps(value)
        except (TypeError, ValueError):
            raise ValueError("el valor no se puede guardar (¿undefined o una función?)") from None
        if len(text.encode("utf-8")) > STORAGE["value"]:
            raise ValueError(f"el valor pesa más de {STORAGE['value'] // 1024} KB")
        with self._lock:
            data = self._load(plugin_id)
            nxt = {**data, str(key): json.loads(text)}
            if len(nxt) > STORAGE["keys"]:
                raise ValueError(f"demasiadas claves (hasta {STORAGE['keys']})")
            if len(json.dumps(nxt).encode("utf-8")) > STORAGE["total"]:
                raise ValueError(f"el almacenamiento pasaría de {STORAGE['total'] // 1024} KB")
            self._cache[plugin_id] = nxt
            self._changed(plugin_id)
        return True

    def remove(self, plugin_id: str, key: Any) -> bool:
        with self._lock:
            data = self._load(plugin_id)
            had = str(key) in data
            if had:
                self._cache[plugin_id] = {k: v for k, v in data.items() if k != str(key)}
                self._changed(plugin_id)
        return had

    def usage(self, plugin_id: str) -> dict[str, int]:
        with self._lock:
            data = self._load(plugin_id)
            return {"bytes": len(json.dumps(data).encode("utf-8")), "keys": len(data)}


# ---------------------------------------------------------------------------------------------------- packages (.tdtplugin)
EXTENSION = ".tdtplugin"
LIMITS = {"files": 200, "total": 8 * 1024 * 1024, "file": 4 * 1024 * 1024, "name": 200}
DOS_DATE = (44 << 9) | (1 << 5) | 1  # 2024-01-01, fixed on purpose


def _u16(n: int) -> bytes:
    return n.to_bytes(2, "little")


def _u32(n: int) -> bytes:
    return (n & 0xFFFFFFFF).to_bytes(4, "little")


def pack_folder(dir_path: Path, plugin_id: str = "") -> bytes:
    """A zip of the folder, with ``<id>/`` as its only top folder. Reproducible: the same folder gives the same bytes."""
    dir_path = Path(dir_path)
    plugin_id = plugin_id or dir_path.name
    if not ID.match(plugin_id):
        raise ValueError("el nombre de la carpeta (el id) solo puede llevar minúsculas, números y guiones")
    plugin = read_plugin(dir_path)
    if plugin["kind"] not in ("ui", "full"):
        raise ValueError('solo se empaquetan plugins con permisos («tdt.api»: 1) o con todo el poder («tdt.full»)')
    if plugin["problems"]:
        raise ValueError("el plugin tiene problemas: " + "; ".join(plugin["problems"]))
    names = files_of(dir_path)
    if len(names) > LIMITS["files"]:
        raise ValueError(f"demasiados archivos (hasta {LIMITS['files']})")
    locals_: list[bytes] = []
    centrals: list[bytes] = []
    offset = 0
    total = 0
    for rel in names:
        raw = (dir_path / rel).read_bytes()
        if len(raw) > LIMITS["file"]:
            raise ValueError(f"«{rel}» pesa más de {LIMITS['file'] // 1048576} MB")
        total += len(raw)
        if total > LIMITS["total"]:
            raise ValueError(f"el plugin pesa más de {LIMITS['total'] // 1048576} MB")
        name = f"{plugin_id}/{rel}".encode("utf-8")
        if len(name) > LIMITS["name"]:
            raise ValueError(f"«{rel}»: el nombre es demasiado largo")
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        deflated = compressor.compress(raw) + compressor.flush()
        stored = len(deflated) >= len(raw)
        data = raw if stored else deflated
        method = 0 if stored else 8
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        common = [_u16(20), _u16(0x0800), _u16(method), _u16(0), _u16(DOS_DATE), _u32(crc), _u32(len(data)), _u32(len(raw)), _u16(len(name)), _u16(0)]
        locals_.append(b"".join([_u32(0x04034B50), *common, name, data]))
        centrals.append(b"".join([
            _u32(0x02014B50), _u16(0x031E), *common, _u16(0), _u16(0), _u16(0),
            _u32((0o100644 << 16) & 0xFFFFFFFF), _u32(offset), name,
        ]))
        offset += len(locals_[-1])
    central = b"".join(centrals)
    end = b"".join([_u32(0x06054B50), _u16(0), _u16(0), _u16(len(names)), _u16(len(names)), _u32(len(central)), _u32(offset), _u16(0)])
    return b"".join([*locals_, central, end])


def _clean_name(raw: str) -> list[str]:
    if not raw or len(raw) > LIMITS["name"] or re.search(r"[\0\\:]", raw) or raw.startswith("/") or re.search(r"[\x01-\x1f]", raw):
        raise ValueError(f"nombre no permitido en el paquete: «{raw[:60]}»")
    parts = raw.split("/")
    if any(not p or p in (".", "..") for p in parts):
        raise ValueError(f"nombre no permitido en el paquete: «{raw[:60]}»")
    return parts


def read_package(buffer: bytes) -> dict[str, Any]:
    """The files of a package: ``{id, files: {path: bytes}}``, with every check a file from anywhere needs."""
    if not isinstance(buffer, (bytes, bytearray)) or len(buffer) < 22 or len(buffer) > LIMITS["total"] * 2:
        raise ValueError("no es un paquete de plugin")
    buffer = bytes(buffer)
    end = -1
    for i in range(len(buffer) - 22, max(-1, len(buffer) - 22 - 65535), -1):
        if buffer[i:i + 4] == b"PK\x05\x06":
            end = i
            break
    if end < 0:
        raise ValueError("no es un archivo zip")
    count = int.from_bytes(buffer[end + 10:end + 12], "little")
    if count == 0 or count > LIMITS["files"]:
        raise ValueError(f"el paquete tiene {count} archivos (hasta {LIMITS['files']})")
    at = int.from_bytes(buffer[end + 16:end + 20], "little")
    files: dict[str, bytes] = {}
    total = 0
    plugin_id: str | None = None
    for _ in range(count):
        if at + 46 > len(buffer) or buffer[at:at + 4] != b"PK\x01\x02":
            raise ValueError("el paquete está dañado")
        flags = int.from_bytes(buffer[at + 8:at + 10], "little")
        method = int.from_bytes(buffer[at + 10:at + 12], "little")
        crc = int.from_bytes(buffer[at + 16:at + 20], "little")
        packed = int.from_bytes(buffer[at + 20:at + 24], "little")
        size = int.from_bytes(buffer[at + 24:at + 28], "little")
        name_len = int.from_bytes(buffer[at + 28:at + 30], "little")
        extra_len = int.from_bytes(buffer[at + 30:at + 32], "little")
        comment_len = int.from_bytes(buffer[at + 32:at + 34], "little")
        external = int.from_bytes(buffer[at + 38:at + 42], "little")
        local = int.from_bytes(buffer[at + 42:at + 46], "little")
        raw_name = buffer[at + 46:at + 46 + name_len].decode("utf-8", "replace")
        at += 46 + name_len + extra_len + comment_len
        if raw_name.endswith("/"):
            continue
        if flags & 1:
            raise ValueError("el paquete está cifrado")
        if 0xFFFFFFFF in (packed, size, local):
            raise ValueError("paquete zip64: demasiado grande")
        if (external >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"«{raw_name[:60]}» es un enlace: no se permiten")
        parts = _clean_name(raw_name)
        if len(parts) < 2:
            raise ValueError("todo el plugin tiene que ir dentro de una carpeta con el nombre del plugin")
        if plugin_id is None:
            plugin_id = parts[0]
        if parts[0] != plugin_id:
            raise ValueError("el paquete tiene más de una carpeta en la raíz")
        if any(p.startswith(".") for p in parts[1:]):
            raise ValueError("no se admiten archivos ocultos")
        if size > LIMITS["file"]:
            raise ValueError(f"«{raw_name[:60]}» pesa más de {LIMITS['file'] // 1048576} MB")
        total += size
        if total > LIMITS["total"]:
            raise ValueError(f"el plugin pesa más de {LIMITS['total'] // 1048576} MB")
        if local + 30 > len(buffer) or buffer[local:local + 4] != b"PK\x03\x04":
            raise ValueError("el paquete está dañado")
        start = local + 30 + int.from_bytes(buffer[local + 26:local + 28], "little") + int.from_bytes(buffer[local + 28:local + 30], "little")
        if start + packed > len(buffer):
            raise ValueError("el paquete está dañado")
        slice_ = buffer[start:start + packed]
        if method == 0:
            data = slice_
        elif method == 8:
            data = zlib.decompressobj(-15).decompress(slice_, size + 1)
        else:
            raise ValueError("el paquete usa una compresión que no se admite")
        if len(data) != size or (zlib.crc32(data) & 0xFFFFFFFF) != crc:
            raise ValueError(f"«{raw_name[:60]}» no coincide con lo que dice el paquete")
        files["/".join(parts[1:])] = data
    if not plugin_id or not ID.match(plugin_id):
        raise ValueError("la carpeta del paquete no tiene un nombre de plugin válido (minúsculas, números y guiones)")
    if "manifest.json" not in files:
        raise ValueError("el paquete no lleva manifest.json")
    return {"id": plugin_id, "files": files}


def inspect_package(buffer: bytes) -> dict[str, Any]:
    """Check a package and say what it contains, in a temporary folder, without installing anything."""
    parsed = read_package(buffer)
    stage = Path(tempfile.mkdtemp(prefix="tdt-plugin-"))
    folder = stage / parsed["id"]
    for name, data in parsed["files"].items():
        target = folder.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"id": parsed["id"], "plugin": read_plugin(folder), "sha256": pluginsign.sha256_hex(buffer), "files": parsed["files"], "stage": stage, "dir": folder}


def place(inspected: dict[str, Any], root: Path) -> Path:
    """Put an inspected package in ``root/<id>`` (replacing that folder) and clean up."""
    target = Path(root) / inspected["id"]
    Path(root).mkdir(parents=True, exist_ok=True)
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(inspected["dir"], target)
    discard(inspected)
    return target


def discard(inspected: dict[str, Any]) -> None:
    shutil.rmtree(inspected["stage"], ignore_errors=True)


# ---------------------------------------------------------------------------------------------------- links and catalogs
CATALOG_LIMITS = {"redirects": 3, "packageBytes": 16 * 1024 * 1024, "catalogBytes": 1_000_000, "timeoutMs": 30_000, "entries": 200}


def check_link(raw: Any) -> dict[str, Any]:
    """May this link be fetched? Only https, port 443, no credentials, a public-looking name (no IP addresses)."""
    try:
        url = urllib.parse.urlsplit(str(raw).strip())
    except ValueError:
        return {"ok": False, "reason": "no es una dirección"}
    if url.scheme != "https":
        return {"ok": False, "reason": "solo se descarga por https"}
    if url.port and url.port != 443:
        return {"ok": False, "reason": "solo el puerto 443"}
    if url.username or url.password:
        return {"ok": False, "reason": "sin usuario ni clave en la dirección"}
    host = (url.hostname or "").lower()
    if not HOST.match(host) or NOT_PUBLIC_NAME.search(host):
        return {"ok": False, "reason": "el servidor tiene que ser un nombre público (no una dirección IP ni de tu red)"}
    if len(urllib.parse.urlunsplit(url)) > 600:
        return {"ok": False, "reason": "la dirección es demasiado larga"}
    return {"ok": True, "url": url}


def _text(value: Any, max: int) -> str:
    return re.sub(r"[\x00-\x1f]", " ", value).strip()[:max] if isinstance(value, str) else ""


def read_catalog(json_data: Any) -> dict[str, Any]:
    """A catalog as published (``{"plugins": [...]}``), cleaned: every entry pins its file with a SHA-256."""
    entries: list[dict[str, Any]] = []
    problems: list[str] = []
    listing = json_data.get("plugins") if isinstance(json_data, dict) else None
    if not isinstance(listing, list):
        return {"entries": entries, "problems": ['el catálogo tiene que ser {"plugins": [...]}']}
    if len(listing) > CATALOG_LIMITS["entries"]:
        problems.append(f"el catálogo pasa de {CATALOG_LIMITS['entries']} plugins: solo se leen los primeros")
    seen: set[str] = set()
    for i, item in enumerate(listing[: CATALOG_LIMITS["entries"]]):
        at = f"plugin {i + 1}"
        if not isinstance(item, dict):
            problems.append(f"{at}: no es un objeto")
            continue
        sha = pluginsign.clean_sha(item.get("sha256"))
        link = check_link(item.get("url"))
        if not ID.match(str(item.get("id"))):
            problems.append(f'{at}: «id» no vale (minúsculas, números y guiones)')
        elif item.get("id") in seen:
            problems.append(f'{at}: «{item.get("id")}» está repetido')
        elif not _text(item.get("name"), 60):
            problems.append(f'{at} («{item.get("id")}»): falta «name»')
        elif not link["ok"]:
            problems.append(f'{at} («{item.get("id")}»): enlace no válido ({link["reason"]})')
        elif not sha:
            problems.append(f'{at} («{item.get("id")}»): falta «sha256» (64 cifras hexadecimales)')
        else:
            seen.add(item["id"])
            signature = pluginsign.parse_signature(item["signature"]) if item.get("signature") is not None else None
            if item.get("signature") is not None and not signature:
                problems.append(f'{at} («{item.get("id")}»): la firma no tiene el formato esperado y se ignora')
            entries.append({
                "signature": signature,
                "fingerprint": pluginsign.fingerprint_of(signature["key"]) if signature else "",
                "id": item["id"], "name": _text(item.get("name"), 60), "author": _text(item.get("author"), 60),
                "version": _text(item.get("version"), 20), "description": _text(item.get("description"), 200),
                "url": urllib.parse.urlunsplit(link["url"]), "sha256": sha,
                "permissions": [_text(p, 80) for p in item["permissions"] if isinstance(p, str)][:12] if isinstance(item.get("permissions"), list) else [],
            })
    return {"entries": entries, "problems": problems}


def with_installed(entries: list[dict[str, Any]], plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        have = next((p for p in plugins if p["id"] == e["id"]), None)
        out.append({**e, "state": "new" if not have else "installed" if str(have.get("version") or "") == e["version"] else "update",
                    "installedVersion": str(have.get("version") or "") if have else ""})
    return out


def download(raw_url: str, max_bytes: int, net: "Net") -> dict[str, Any]:
    """Download ``raw_url`` (at most ``max_bytes``) from a public server only: the link is checked, and every address
    it resolves to is checked to be public at the moment of connecting (``net`` does that). Each redirect is
    re-validated the same way. Returns ``{url, buffer}``."""
    target = str(raw_url)
    for _hop in range(CATALOG_LIMITS["redirects"] + 1):
        check = check_link(target)
        if not check["ok"]:
            raise ValueError(check["reason"])
        url = urllib.parse.urlunsplit(check["url"])
        try:
            response = net.open(url, {"Accept": "*/*"}, require_allowed=False, timeout=CATALOG_LIMITS["timeoutMs"] / 1000, follow_redirects=False)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400 and exc.headers.get("Location"):
                target = urllib.parse.urljoin(url, exc.headers["Location"])
                exc.close()
                continue
            raise ValueError(f"el servidor contestó {exc.code}") from exc
        except OSError as exc:
            raise ValueError(f"no se pudo conectar con {check['url'].hostname}: {exc}") from exc
        with response:
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"el archivo pesa más de {round(max_bytes / 1048576, 1)} MB")
                chunks.append(chunk)
            return {"url": url, "buffer": b"".join(chunks)}
    raise ValueError("demasiadas redirecciones")


# ---------------------------------------------------------------------------------------------------- templates for «create a plugin»
def id_from_name(name: Any, taken: list[str] | None = None) -> str:
    base = unicodedata.normalize("NFD", str(name or ""))
    base = "".join(c for c in base if not unicodedata.combining(c)).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:30].rstrip("-") or "mi-plugin"
    pid = base
    n = 2
    for taken_id in taken or []:
        if pid == taken_id:
            pid = f"{base[:36]}-{n}"
            n += 1
    return pid


def new_plugin_files(plugin_id: str, name: str, kind: str = "ui") -> dict[str, str]:
    """The folder of a new plugin: what «Crear un plugin nuevo» (and the theme / full-power buttons) writes."""
    title = re.sub(r"[<>&\"']", "", str(name or plugin_id))[:60]
    if kind == "full":
        return _full_template(title)
    if kind == "theme":
        return _theme_template(title)
    manifest = {
        "name": title, "version": "0.1.0", "description": "Dime en una frase qué hace.", "author": "Tu nombre",
        "tdt": {"api": 1, "entry": "index.html", "label": title[:24], "panel": {"width": 320, "height": 180}, "permissions": ["player.read", "ui.toast"]},
    }
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "index.html": f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="color-scheme" content="light dark">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ margin: 0; padding: 12px; font: 14px/1.4 system-ui, sans-serif; color: CanvasText; background: transparent; }}
    button {{ font: inherit; }}
  </style>
</head>
<body>
  <h1 style="font-size: 18px; margin: 0 0 6px">{title}</h1>
  <p id="texto">Conectando con TDT local…</p>
  <button id="aviso" type="button">Decir hola</button>
  <script src="/@tdt/sdk.js"></script>
  <script src="app.js"></script>
</body>
</html>
""",
        "app.js": """// 1. `tdt` lo crea /@tdt/sdk.js. Todo lo que pides pasa por la aplicación, que solo contesta a lo que declaraste en
//    "permissions" del manifest.json y aprobó quien te activó.
// 2. Cambia el manifest (permisos, tamaño del panel) y pulsa «Buscar de nuevo» en Ajustes ▸ Plugins: se recarga solo.
// 3. Lo que escribas con console.log sale en Ajustes ▸ Registro, con el nombre de tu plugin.
const $ = (id) => document.getElementById(id);

function pintar(estado) {
  const activa = estado.tiles.find((t) => t.slot === estado.focus) || estado.tiles[0];
  $('texto').textContent = activa ? 'Estás viendo ' + activa.name : 'No hay nada en pantalla';
}

(async () => {
  const yo = await tdt.ready; // { id, name, version, permissions }
  console.log('listo:', yo.id, yo.permissions.join(', '));
  pintar(await tdt.player.state()); // permiso player.read
  tdt.on('player', pintar); // la aplicación avisa cuando cambia algo
  $('aviso').addEventListener('click', () => tdt.ui.toast('Hola desde ' + yo.name).catch(console.error)); // permiso ui.toast
})().catch((error) => {
  $('texto').textContent = error.message; // por ejemplo: permiso denegado: player.read
});
""",
    }


def _manifest_of(title: str, description: str, full: dict[str, Any]) -> str:
    return json.dumps({"name": title, "version": "0.1.0", "description": description, "author": "Tu nombre", "tdt": {"api": 1, "full": full}}, ensure_ascii=False, indent=2) + "\n"


def _full_template(title: str) -> dict[str, str]:
    return {
        "manifest.json": _manifest_of(title, "Dime en una frase qué hace.", {"page": "page.js"}),
        "page.js": """// Un plugin con TODO el poder: corre dentro de la página de TDT local, sin aislar, con los ganchos de la aplicación (docs/HACKEABLE.md).
// 1. `tdt` lo pone la aplicación: tdt.on (algo ha pasado), tdt.filter (cambiar un valor), tdt.register (añadir o reemplazar cosas) y tdt.log (sale en Ajustes ▸ Registro con tu nombre).
// 2. Este ejemplo escribe en el Registro cuando empieza una pausa de anuncios y pone el nombre del canal sobre cada casilla.
// 3. Se ejecuta dentro de una función async: puedes usar await al principio y terminar con return () => { ... } para limpiar al apagarlo.
// 4. Al activarlo se te enseña este código y su huella; si cambias una sola letra, se te vuelve a preguntar.
tdt.on('ad.started', (anuncio) => tdt.log('pausa de anuncios en', anuncio.name));

tdt.register('tile.overlay', 'etiqueta', {
  mount(ctx) {
    const el = document.createElement('div');
    el.style.cssText = 'position:absolute;left:8px;bottom:8px;padding:2px 8px;border-radius:4px;background:rgba(0,0,0,.6);color:#fff;font:12px system-ui';
    const pintar = (c) => { el.textContent = c.tile.channel ? c.tile.channel.name : ''; el.hidden = !c.tile.channel; };
    pintar(ctx);
    return { el, update: pintar };
  },
});
""",
    }


def _theme_template(title: str) -> dict[str, str]:
    return {
        "manifest.json": _manifest_of(title, "Dime en una frase cómo lo deja.", {"css": "tema.css"}),
        "tema.css": """/* Un tema: una hoja de estilo que cambia el aspecto de TDT local (docs/TEMAS.md). Se aplica después de la de la aplicación y no ejecuta código. */
:root { --accent: #c0392b; --accent-ink: #ffffff; }
@media (prefers-color-scheme: dark) { :root { --accent: #e0705e; --accent-ink: #1a0f0d; } }

/* Más colores: --chassis --panel --ink --muted --line --screen --row-hover --ok --bad --unk, y --font para la tipografía.
   Y cualquier clase estable (.tile-bar, .btn, .list, .notice...): mira la lista en docs/TEMAS.md. Una imagen de tu carpeta: background-image: url(fondo.png). */
""",
    }


# ---------------------------------------------------------------------------------------------------- the store: state, serving, what a plugin may ask
# The policy of a plugin page (desktop/lib/plugin-protocol.js): it can run its own scripts and styles and show its
# own images, fonts and sound, and nothing else: no requests, no frames, no forms, no <base>, no plugins.
PLUGIN_POLICY = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; media-src 'self' data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
)
# the runner module of a full plugin: imported by the page as a module (no eval), it fetches nothing
FULL_RUN_POLICY = "default-src 'none'; " + "script-src 'self'"

PLUGIN_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".mp4": "video/mp4", ".webm": "video/webm",
}
SDK_PATH = "/@tdt/sdk.js"
MAX_PLUGIN_FILE = 8 * 1024 * 1024
MAX_INSTALL_BYTES = 16 * 1024 * 1024
FETCH = {"redirects": 3, "bodyIn": 100_000, "bodyOut": 1_000_000, "timeoutMs": 15_000, "headers": ("accept", "accept-language", "content-type")}


def sdk_file() -> Path:
    """Where the SDK the plugin pages load (``/@tdt/sdk.js``) lives."""
    for candidate in (PROJECT_DIR / "desktop" / "sdk" / "tdt-sdk.js", PACKAGE_DIR / "static" / "plugin-sdk.js"):
        if candidate.is_file():
            return candidate
    return PROJECT_DIR / "desktop" / "sdk" / "tdt-sdk.js"


def frame_ancestors(config: "Config") -> str:
    """Who may frame a plugin page: the app's own origin (loopback by default, the LAN address with --lan) and loopback."""
    return "'self' http://127.0.0.1:* http://localhost:*"


class PluginError(Exception):
    """An answer with a code the plugin's SDK can tell apart."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _code_of(message: str) -> str:
    m = str(message)
    if m.startswith("permiso denegado"):
        return "E_PERMISSION"
    if m.startswith("demasiadas peticiones"):
        return "E_RATE"
    if re.search(r"pesa más|pasaría de|demasiadas claves", m):
        return "E_QUOTA"
    if re.search(r"solo se puede pedir por https|solo el puerto|sin usuario|dirección no válida|solo GET|cuerpo no permitido|la clave puede|no se puede guardar", m):
        return "E_INVALID"
    return "E_UNAVAILABLE"


def plugin_fetch(permissions: list[str], raw_url: Any, init: Any, net: "Net") -> dict[str, Any]:
    """One https request a plugin asked for: only hosts its permissions cover, only public addresses, up to 3
    redirects each checked against the permissions, a short list of headers, 100 KB of body at most and 1 MB of answer."""
    method = str((init or {}).get("method") or "GET").upper() if isinstance(init, dict) else "GET"
    if method not in ("GET", "POST"):
        raise PluginError("E_INVALID", "solo GET y POST")
    headers = {}
    for key, value in ((init or {}).get("headers") or {}).items() if isinstance(init, dict) else []:
        if key.lower() in FETCH["headers"] and isinstance(value, str) and len(value) < 300:
            headers[key] = value
    body = (init or {}).get("body") if isinstance(init, dict) else None
    if body is not None:
        if method != "POST" or len(str(body).encode("utf-8")) > FETCH["bodyIn"]:
            raise PluginError("E_INVALID", "cuerpo no permitido (solo POST, hasta 100 KB)")
        body = str(body)
    target = str(raw_url)
    for hop in range(FETCH["redirects"] + 1):
        check = check_link(target)  # https, 443, no credentials, public name
        if not check["ok"]:
            raise PluginError(_code_of(check["reason"]), check["reason"])
        url = urllib.parse.urlunsplit(check["url"])
        host = (check["url"].hostname or "").lower()
        if not network_allows(permissions, host):
            raise PluginError("E_PERMISSION", f"permiso denegado: network:{host}")
        try:
            response = net.open(url, headers, require_allowed=False, timeout=FETCH["timeoutMs"] / 1000,
                                follow_redirects=False, strip_on_redirect=())
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400 and exc.headers.get("Location"):
                target = urllib.parse.urljoin(url, exc.headers["Location"])
                exc.close()
                continue
            return {"status": exc.code, "ok": False, "url": url, "contentType": "", "text": ""}
        with response:
            status = response.status
            location = None
            if 300 <= status < 400:
                location = response.headers.get("Location")
            if location:
                target = urllib.parse.urljoin(url, location)
                continue
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > FETCH["bodyOut"]:
                    raise PluginError("E_QUOTA", "la respuesta pesa más de 1 MB")
                chunks.append(chunk)
            return {
                "status": status, "ok": 200 <= status < 300, "url": url,
                "contentType": (response.headers.get("Content-Type") or "")[:120],
                "text": b"".join(chunks).decode("utf-8", "replace"),
            }
    raise PluginError("E_UNAVAILABLE", "demasiadas redirecciones")


class PluginStore:
    """The plugins of the browser mode: what is found, what the user turned on and approved, what is served to the
    plugin frames, and what a plugin may ask of the server (its storage, its config, its network)."""

    def __init__(self, config: "Config", net: "Net") -> None:
        self.config = config
        self.net = net
        self.data_dir = Path(config.data_dir)
        self.dir = self.data_dir / "browser"
        self.user_dir = self.data_dir / "plugins"
        self.state_file = self.dir / "plugins.json"
        self.approved_file = self.dir / "ui.json"
        self.keys_file = self.dir / "plugin-keys.json"
        self.staging_dir = self.dir / "staging"
        self.config_store = ConfigStore(self.dir / "plugin-config.json")
        self.storage = PluginStorage(self.dir / "plugin-data")
        self.lock = threading.RLock()
        self.plugins: list[dict[str, Any]] = []
        self.enabled: set[str] = set()
        self.approved: dict[str, str] = {}
        self.denied: dict[str, list[str]] = {}
        self.keys: dict[str, Any] = {}
        self.activity: dict[str, dict[str, Any]] = {}
        self.staging: dict[str, dict[str, Any]] = {}
        self._fetching: dict[str, int] = {}
        self.safe = ""  # safe mode text (safemode.py): no plugin runs and nothing changes while it is set
        self.scan()

    def set_safe(self, text: str) -> None:
        """Safe mode on (``text`` says why) or off (""). While it is on no plugin is on and no change is accepted."""
        with self.lock:
            self.safe = text or ""

    def _safe_refusal(self) -> dict[str, Any] | None:
        if self.safe:
            return {"ok": False, "detail": f"modo seguro: no se cambia nada de los plugins hasta salir de él ({self.safe})"}
        return None

    # ---- discovery and state files
    def roots(self) -> list[dict[str, Any]]:
        extra = [d for d in (os.environ.get("TDT_PLUGINS_DIR") or "").split(";" if os.name == "nt" else ":") if d]
        bundled = PROJECT_DIR / "tools"
        roots = [{"dir": str(Path(d)), "label": "TDT_PLUGINS_DIR"} for d in extra]
        roots.append({"dir": str(self.user_dir), "label": "tu carpeta de plugins"})
        if bundled.is_dir():
            roots.append({"dir": str(bundled), "label": "incluido con la aplicación"})
        return roots

    def scan(self) -> None:
        with self.lock:
            self.plugins = discover(self.roots())
            self.enabled = self._read_enabled()
            approved, denied = self._read_approved()
            self.approved, self.denied = approved, denied
            self.keys = self._read_keys()

    def _read_enabled(self) -> set[str]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return {x for x in data.get("enabled", []) if isinstance(x, str)} if isinstance(data, dict) else set()
        except (OSError, ValueError):
            return set()

    def _write_enabled(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"enabled": sorted(self.enabled)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _read_approved(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        try:
            data = json.loads(self.approved_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}, {}
        if not isinstance(data, dict):
            return {}, {}
        approved = {k: v for k, v in (data.get("approved") or {}).items() if isinstance(k, str) and isinstance(v, str)}
        denied = {k: [x for x in v if isinstance(x, str)] for k, v in (data.get("denied") or {}).items() if isinstance(v, list)}
        return approved, denied

    def _write_approved(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        kept = {k: v for k, v in self.denied.items() if v}
        tmp = self.approved_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"approved": self.approved, "denied": kept}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.approved_file)

    def _read_keys(self) -> dict[str, Any]:
        try:
            data = json.loads(self.keys_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_keys(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.keys_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.keys, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.keys_file)

    def find(self, plugin_id: str) -> dict[str, Any] | None:
        return next((p for p in self.plugins if p["id"] == plugin_id), None)

    def is_on(self, p: dict[str, Any]) -> bool:
        if self.safe:
            return False  # safe mode: nothing runs, the file of choices is not touched
        if p["problems"]:
            return False
        if p["id"] not in self.enabled:
            return False
        if p["kind"] in ("ui", "full"):
            return is_approved(p, self.approved)
        return True

    def active_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        p = self.find(plugin_id)
        return p if p and p["kind"] == "ui" and self.is_on(p) else None

    def effective(self, p: dict[str, Any]) -> list[str]:
        return effective_permissions(p, self.denied)

    def activity_of(self, plugin_id: str) -> dict[str, Any]:
        if plugin_id not in self.activity:
            self.activity[plugin_id] = {"fetches": 0, "bytesIn": 0, "hosts": []}
        return self.activity[plugin_id]

    # ---- what the settings page and the MCP are told (like pluginList() of desktop/main.js)
    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            plugins = []
            for p in self.plugins:
                item: dict[str, Any] = {
                    "id": p["id"], "name": p["name"], "version": p["version"], "description": p["description"],
                    "source": p["source"], "sites": p["sites"], "problems": p["problems"],
                    "enabled": self.is_on(p), "kind": p["kind"],
                }
                if p["kind"] == "full":
                    item.update({
                        "author": p.get("author", ""), "homepage": p.get("homepage", ""), "scopes": p["scopes"],
                        "needsApproval": needs_reapproval(p, self.approved), "hash": p.get("hash", "")[:16],
                    })
                elif p["kind"] == "ui":
                    item.update({
                        "author": p.get("author", ""), "homepage": p.get("homepage", ""),
                        "panel": bool(p.get("panel")), "screen": bool(p.get("panel") and p["panel"].get("placement") == "screen"),
                        "placement": "none" if not p.get("panel") else "screen" if p["panel"].get("placement") == "screen" else "float",
                        "permissions": [
                            {"name": name, "text": describe_permission(name), "risk": RISK_TEXT[risk_of(name)],
                             "granted": name not in self.denied.get(p["id"], [])}
                            for name in p.get("permissions", [])
                        ],
                        "activity": {**self.activity_of(p["id"]), "storage": self.storage.usage(p["id"])},
                        "settings": form_of(p.get("settings", {}), self.config_store.get(p["id"])),
                        "activation": p.get("activation", []), "sources": p.get("sources", []),
                        "themes": [str(t.get("name", ""))[:40] for t in p.get("themes", [])],
                        "rulesBrought": len(p.get("rules", [])),
                        "needsApproval": needs_reapproval(p, self.approved),
                    })
                plugins.append(item)
            return {
                "safeMode": self.safe,  # set by safemode.py (crash guard, --safe, or the settings)
                "mode": "browser",
                "folder": str(self.user_dir),
                "plugins": plugins,
            }

    # ---- the operations the page (and later the MCP) asks for
    def enable(self, plugin_id: str, on: bool) -> dict[str, Any]:
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            p = self.find(plugin_id)
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin con ese id"}
            if on and p["problems"]:
                return {"ok": False, "detail": "el plugin tiene problemas y no se puede activar: " + "; ".join(p["problems"])}
            if on and p["kind"] in ("ui", "full") and not is_approved(p, self.approved):
                return {"ok": False, "detail": "necesita aprobación del usuario", "needsApproval": True}
            if on:
                self.enabled.add(plugin_id)
            else:
                self.enabled.discard(plugin_id)
            self._write_enabled()
            return {"ok": True, "detail": "activado" if on else "desactivado", "list": self.snapshot()}

    def approve(self, plugin_id: str, sig: Any) -> dict[str, Any]:
        """The user approved this plugin with this signature: it is only kept if it is exactly what the folder says now."""
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            p = self.find(plugin_id)
            if not p or p["kind"] not in ("ui", "full"):
                return {"ok": False, "detail": "no hay ningún plugin de ese tipo con ese id"}
            if p["problems"]:
                return {"ok": False, "detail": "el plugin tiene problemas: " + "; ".join(p["problems"])}
            want = full_signature(p) if p["kind"] == "full" else signature(p)
            if sig != want:
                return {"ok": False, "detail": "la firma no corresponde a lo que hay ahora en la carpeta"}
            self.approved[plugin_id] = want
            self._write_approved()
            return {"ok": True, "detail": "aprobado", "list": self.snapshot()}

    def review_of(self, plugin_id: str) -> dict[str, Any]:
        """What the page shows before the user approves a plugin that is already on disk: its signature (so the page can
        send it back on approve), what it asks for, and for a full plugin a preview of its code and its fingerprint."""
        with self.lock:
            p = self.find(plugin_id)
            if not p or p["kind"] not in ("ui", "full"):
                return {"error": "no hay ningún plugin con permisos o con todo el poder con ese id"}
            if p["problems"]:
                return {"error": "el plugin tiene problemas: " + "; ".join(p["problems"])}
            full = p["kind"] == "full"
            theme = full and "css" in p.get("scopes", []) and "page" not in p.get("scopes", [])
            asks = "\n".join(f"  • {describe_permission(x)}" for x in p.get("permissions", [])) or "  • ninguno"
            preview = ""
            if full:
                src = p.get("files", {}).get("page") or p.get("files", {}).get("css")
                if src:
                    text = Path(src).read_text(encoding="utf-8", errors="replace")
                    preview = text[:1200] + (f"\n… ({len(text) - 1200} caracteres más)" if len(text) > 1200 else "")
            power = (
                "Es un tema: una hoja de estilo que cambia el aspecto de TDT local y no ejecuta código."
                if theme else
                "Es un plugin con TODO el poder: corre DENTRO de la página de TDT local, sin aislar y sin permisos que lo limiten."
            ) if full else ""
            return {
                "id": p["id"], "name": p["name"], "version": p["version"], "author": p.get("author", ""),
                "description": p.get("description", ""), "kind": p["kind"], "full": full, "theme": theme, "power": power,
                "asks": asks, "preview": preview,
                "signature": full_signature(p) if full else signature(p),
                "hash": p.get("hash", ""), "sha256": fingerprint(Path(p["dir"])) if full else "",
                "needsApproval": needs_reapproval(p, self.approved), "enabled": self.is_on(p),
                "installs": "Activarlo es lo que falta: al aceptarlo se enciende con exactamente lo que aquí se ve.",
            }

    def set_permission(self, plugin_id: str, name: str, on: bool) -> dict[str, Any]:
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            p = self.find(plugin_id)
            if not p or p["kind"] != "ui" or name not in p.get("permissions", []):
                return {"ok": False, "detail": "el plugin no declara ese permiso"}
            off = set(self.denied.get(plugin_id, []))
            if on:
                off.discard(name)
            else:
                off.add(name)
            self.denied[plugin_id] = sorted(off)
            self._write_approved()
            return {"ok": True, "detail": "permitido" if on else "quitado", "list": self.snapshot()}

    def set_config(self, plugin_id: str, changes: Any) -> dict[str, Any]:
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            p = self.find(plugin_id)
            if not p or p["kind"] != "ui":
                return {"ok": False, "error": "no hay ningún plugin con permisos con ese id"}
            done = apply_changes(p.get("settings", {}), self.config_store.get(plugin_id), changes)
            if not done["ok"]:
                return {"ok": False, "error": done["error"]}
            self.config_store.set(plugin_id, done["values"])
            return {"ok": True, "values": done["values"], "list": self.snapshot()}

    def create(self, name: Any, kind: str) -> dict[str, Any]:
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            pid = id_from_name(name, [p["id"] for p in self.plugins])
            folder = self.user_dir / pid
            if folder.exists():
                return {"ok": False, "error": "ya existe una carpeta con ese nombre"}
            folder.mkdir(parents=True, exist_ok=True)
            for fname, text in new_plugin_files(pid, str(name or "").strip()[:60] or pid, kind if kind in ("ui", "full", "theme") else "ui").items():
                (folder / fname).write_text(text, encoding="utf-8")
            self.scan()
            return {"ok": True, "id": pid, "folder": str(folder), "list": self.snapshot()}

    def uninstall(self, plugin_id: str) -> dict[str, Any]:
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            p = self.find(plugin_id)
            if not p:
                return {"ok": False, "detail": "no hay ningún plugin con ese id"}
            folder = Path(p["dir"])
            if self.user_dir not in folder.parents:
                return {"ok": False, "detail": "solo se pueden desinstalar los plugins de tu carpeta"}
            self.enabled.discard(plugin_id)
            self.approved.pop(plugin_id, None)
            self.denied.pop(plugin_id, None)
            self._write_enabled()
            self._write_approved()
            shutil.rmtree(folder, ignore_errors=True)
            self.scan()
            return {"ok": True, "detail": "desinstalado", "list": self.snapshot()}

    def pack(self, plugin_id: str) -> dict[str, Any]:
        with self.lock:
            p = self.find(plugin_id)
            if not p or p["kind"] not in ("ui", "full"):
                return {"ok": False, "error": "solo se empaquetan plugins con permisos o con todo el poder"}
            try:
                buffer = pack_folder(Path(p["dir"]), p["id"])
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "bytes": len(buffer), "sha256": pluginsign.sha256_hex(buffer), "buffer": buffer,
                    "filename": f"{p['id']}-{p.get('version') or '0'}{EXTENSION}"}

    # ---- installing: inspect first (nothing is copied), commit only after the user says yes
    def _review(self, inspected: dict[str, Any], origin: str, pinned: bool, expected_sha: str | None, raw_signature: Any) -> dict[str, Any]:
        plugin = inspected["plugin"]
        verdict = pluginsign.judge(inspected["id"], inspected.get("buffer", b""), raw_signature, self.keys)
        old = self.find(inspected["id"])
        full = plugin["kind"] == "full"
        theme = full and "css" in plugin.get("scopes", []) and "page" not in plugin.get("scopes", [])
        asks = "\n".join(f"  • {describe_permission(p)}" for p in plugin.get("permissions", [])) or "  • ninguno"
        preview = ""
        if full:
            f = plugin.get("files", {})
            src = f.get("page") or f.get("css")
            if src:
                text = Path(src).read_text(encoding="utf-8", errors="replace")
                preview = text[:1200] + (f"\n… ({len(text) - 1200} caracteres más)" if len(text) > 1200 else "")
        power = (
            "Es un tema: una hoja de estilo que cambia el aspecto de TDT local y no ejecuta código."
            if theme else
            "Es un plugin con TODO el poder: corre DENTRO de la página de TDT local, sin aislar y sin permisos que lo limiten."
        ) if full else ""
        return {
            "token": None,  # filled by the caller
            "id": inspected["id"], "name": plugin["name"], "version": plugin["version"], "author": plugin.get("author", ""),
            "description": plugin.get("description", ""), "kind": plugin["kind"], "problems": plugin["problems"],
            "full": full, "theme": theme, "power": power, "asks": asks, "preview": preview,
            "origin": origin, "sha256": inspected["sha256"], "pinned": pinned, "expectedSha": expected_sha,
            "signature": {"state": verdict["state"], "fingerprint": verdict.get("fingerprint", ""), "previous": verdict.get("previous", ""),
                          "reason": verdict.get("reason", ""), "warning": pluginsign.is_warning(verdict),
                          "text": pluginsign.describe_judgement(verdict)},
            "replaces": bool(old), "oldVersion": (old or {}).get("version", ""),
            "installs": "Instalarlo no lo activa: al activarlo se te enseñarán sus permisos (o su código) y decides.",
        }

    def inspect_bytes(self, buffer: bytes, raw_signature: Any = None) -> dict[str, Any]:
        """Check a package that came from a file the user picked: it is staged, nothing is installed yet."""
        if len(buffer) > MAX_INSTALL_BYTES:
            return {"error": "el archivo pesa demasiado"}
        try:
            inspected = inspect_package(buffer)
        except (ValueError, OSError) as exc:
            return {"error": str(exc)}
        inspected["buffer"] = buffer
        token = secrets.token_hex(16)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        (self.staging_dir / f"{token}.pkg").write_bytes(buffer)
        review = self._review(inspected, "", False, None, raw_signature)
        review["token"] = token
        self.staging[token] = {"inspected": inspected, "review": review}
        return review

    def inspect_link(self, raw_url: Any, raw_sha: Any = None, raw_signature: Any = None,
                     fetcher: Callable[[str, int], dict[str, Any]] | None = None) -> dict[str, Any]:
        """Download a package from a link (https, public host, pinned with a SHA-256 if the user gave one) and inspect it."""
        check = check_link(raw_url)  # before any fetch, whoever fetches: only public https on port 443
        if not check["ok"]:
            return {"error": check["reason"]}
        expected = pluginsign.clean_sha(raw_sha)
        if raw_sha and not expected:
            return {"error": "el SHA-256 tiene que ser de 64 cifras hexadecimales"}
        fetch = fetcher or (lambda url, max_bytes: download(url, max_bytes, self.net))
        try:
            got = fetch(str(raw_url), CATALOG_LIMITS["packageBytes"])
        except (ValueError, OSError) as exc:
            return {"error": f"no se pudo descargar: {exc}"}
        digest = pluginsign.sha256_hex(got["buffer"])
        if expected and digest != expected:
            return {"error": f"el archivo descargado no es el que se esperaba: su SHA-256 es {digest}"}
        signature = raw_signature if isinstance(raw_signature, (dict, str)) else None
        if not signature:
            try:
                sig = fetch(got["url"] + ".sig", 4096)
                signature = sig["buffer"].decode("utf-8", "replace")
            except (ValueError, OSError):
                signature = None
        try:
            inspected = inspect_package(got["buffer"])
        except (ValueError, OSError) as exc:
            return {"error": str(exc)}
        inspected["buffer"] = got["buffer"]
        token = secrets.token_hex(16)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        (self.staging_dir / f"{token}.pkg").write_bytes(got["buffer"])
        origin = urllib.parse.urlsplit(got["url"]).hostname or ""
        review = self._review(inspected, origin, bool(expected), expected, signature)
        review["token"] = token
        self.staging[token] = {"inspected": inspected, "review": review}
        return review

    def commit(self, token: Any) -> dict[str, Any]:
        """Install what was inspected, only after the user said yes (the page asks with the review in front)."""
        with self.lock:
            refused = self._safe_refusal()
            if refused:
                return refused
            staged = self.staging.pop(str(token), None)
            pkg = self.staging_dir / f"{str(token)}.pkg"
            if not staged or not pkg.is_file():
                return {"ok": False, "error": "esa instalación ya no está pendiente (o caducó)"}
            buffer = pkg.read_bytes()
            pkg.unlink()
            review = staged["review"]
            if review.get("signature", {}).get("state") == "invalid":
                discard(staged["inspected"])
                return {"ok": False, "error": "la firma no es válida: " + review["signature"].get("reason", "")}
            if review.get("expectedSha") and pluginsign.sha256_hex(buffer) != review["expectedSha"]:
                discard(staged["inspected"])
                return {"ok": False, "error": "el archivo cambió desde que se inspeccionó"}
            if review.get("problems"):
                discard(staged["inspected"])
                return {"ok": False, "error": "el plugin tiene problemas: " + "; ".join(review["problems"])}
            inspected = staged["inspected"]
            place(inspected, self.user_dir)
            fingerprint = review.get("signature", {}).get("fingerprint", "")
            if fingerprint:
                self.keys[inspected["id"]] = {"fingerprint": fingerprint, "author": str(review.get("author") or "")[:60],
                                               "since": time.strftime("%Y-%m-%d")}
                self._write_keys()
            elif review.get("signature", {}).get("state") == "unsigned-was-signed":
                self.keys.pop(inspected["id"], None)
                self._write_keys()
            self.scan()
            return {"ok": True, "id": inspected["id"], "sha256": review["sha256"],
                    "signature": review.get("signature", {}), "list": self.snapshot()}

    def catalog(self, raw_url: Any, fetcher: Callable[[str, int], dict[str, Any]] | None = None) -> dict[str, Any]:
        fetch = fetcher or (lambda url, max_bytes: download(url, max_bytes, self.net))
        try:
            got = fetch(str(raw_url), CATALOG_LIMITS["catalogBytes"])
            data = json.loads(got["buffer"].decode("utf-8"))
        except (ValueError, OSError) as exc:
            return {"error": f"no se pudo leer el catálogo: {exc}"}
        parsed = read_catalog(data)
        with self.lock:
            entries = with_installed(parsed["entries"], self.plugins)
        return {"source": urllib.parse.urlsplit(got["url"]).hostname or "", "entries": entries, "problems": parsed["problems"]}

    # ---- serving the files of a running plugin (the frame's src is /plugin/<id>/…)
    def serve(self, plugin_id: str, path: str) -> tuple[int, str, bytes]:
        """One file of a plugin with permissions that is running: only files inside its folder, never hidden ones."""
        p = self.active_plugin(plugin_id)
        if not p:
            return 404, "text/plain; charset=utf-8", "este plugin no está activado".encode("utf-8")
        if path == SDK_PATH:
            sdk = sdk_file()
            if not sdk.is_file():
                return 404, "text/plain; charset=utf-8", b"no existe"
            return 200, PLUGIN_TYPES[".js"], sdk.read_bytes()
        wanted = path.lstrip("/") or p.get("entry") or "index.html"
        if any(part.startswith(".") for part in wanted.split("/")):
            return 404, "text/plain; charset=utf-8", b"no existe"
        real = inside_of(Path(p["dir"]), wanted)
        if not real or not real.is_file() or real.stat().st_size > MAX_PLUGIN_FILE:
            return 404, "text/plain; charset=utf-8", b"no existe"
        ctype = PLUGIN_TYPES.get(real.suffix.lower())
        if not ctype:
            return 415, "text/plain; charset=utf-8", b"tipo de archivo no permitido"
        return 200, ctype, real.read_bytes()

    def serve_full_run(self, plugin_id: str) -> tuple[int, str, bytes]:
        """The runner module of a full plugin (``/plugin-full/<id>/run.js``): the page imports it as a module — the only
        way to run code under a ``script-src 'self'`` policy, no eval anywhere. It is served only while the plugin is on
        AND its fingerprint still matches the approval: a file changed on disk after the approval gets a 409, never code."""
        with self.lock:
            p = self.find(plugin_id)
            if not p or p["kind"] != "full" or p["id"] not in self.enabled:
                return 404, "text/plain; charset=utf-8", "este plugin no está activado".encode("utf-8")
            fresh = read_plugin(Path(p["dir"]))
            if not is_approved(fresh, self.approved):
                return 409, "text/plain; charset=utf-8", "la huella del código cambió desde que se aprobó: no corre".encode("utf-8")
            page = fresh.get("files", {}).get("page")
            if not page:
                return 404, "text/plain; charset=utf-8", "no tiene alcance «page»".encode("utf-8")
            try:
                code = Path(page).read_text(encoding="utf-8")
                if fingerprint(Path(fresh["dir"])) != fresh["hash"]:
                    return 409, "text/plain; charset=utf-8", "cambió mientras se leía".encode("utf-8")
            except (OSError, ValueError):
                return 409, "text/plain; charset=utf-8", "no se pudo leer".encode("utf-8")
            name = json.dumps(fresh["id"])
            body = (
                "// Generado por el servidor a partir de la carpeta del plugin, cuya huella está aprobada.\n"
                f"const run = async (tdt) => {{\n"
                f"  if (!tdt.error) tdt.error = (e) => console.error('[plugin:' + {name} + ']', e);\n"
                + code
                + "\n};\n"
                + f"if (window.tdtPluginHost) window.tdtPluginHost.load({name}, run, 'full');\n"
                + f"else (window.__tdtQueue = window.__tdtQueue || []).push([{name}, run, 'full']);\n"
            ).encode("utf-8")
            return 200, "text/javascript; charset=utf-8", body

    # ---- what the page asks of the server for a plugin (storage, config, network): the permission is checked again
    def call(self, plugin_id: str, method: str, args: list[Any]) -> dict[str, Any]:
        p = self.active_plugin(plugin_id)
        if not p:
            return {"ok": False, "error": "el plugin no está activado", "code": "E_UNAVAILABLE"}
        allowed = self.effective(p)
        try:
            if method in ("config.get", "config.all"):
                values = values_of(p.get("settings", {}), self.config_store.get(plugin_id))
                if method == "config.all":
                    return {"ok": True, "result": values}
                if not isinstance(args[0] if args else None, str) or args[0] not in values:
                    return {"ok": False, "error": f'no hay ningún ajuste «{str(args[0] if args else "")[:40]}»', "code": "E_NOT_FOUND"}
                return {"ok": True, "result": values[args[0]]}
            if method.startswith("storage."):
                if "storage" not in allowed:
                    return {"ok": False, "error": "permiso denegado: storage", "code": "E_PERMISSION"}
                key = args[0] if args else None
                if method == "storage.get":
                    return {"ok": True, "result": self.storage.get(plugin_id, key)}
                if method == "storage.set":
                    self.storage.set(plugin_id, key, args[1] if len(args) > 1 else None)
                    return {"ok": True, "result": True}
                if method == "storage.remove":
                    return {"ok": True, "result": self.storage.remove(plugin_id, key)}
                if method == "storage.keys":
                    return {"ok": True, "result": self.storage.keys(plugin_id)}
            elif method == "net.fetch":
                if not any(a.startswith("network:") for a in allowed):
                    return {"ok": False, "error": "permiso denegado: network", "code": "E_PERMISSION"}
                with self.lock:
                    if self._fetching.get(plugin_id, 0) >= 4:
                        return {"ok": False, "error": "demasiadas peticiones a la vez", "code": "E_RATE"}
                    self._fetching[plugin_id] = self._fetching.get(plugin_id, 0) + 1
                try:
                    result = plugin_fetch(allowed, args[0] if args else "", args[1] if len(args) > 1 else {}, self.net)
                    seen = urllib.parse.urlsplit(result["url"])
                    mine = self.activity_of(plugin_id)
                    mine["fetches"] += 1
                    mine["bytesIn"] += len(result["text"].encode("utf-8"))
                    mine["hosts"] = [seen.hostname or ""] + [h for h in mine["hosts"] if h != seen.hostname][:4]
                    return {"ok": True, "result": result}
                finally:
                    with self.lock:
                        self._fetching[plugin_id] -= 1
            return {"ok": False, "error": "método desconocido", "code": "E_UNSUPPORTED"}
        except PluginError as exc:
            return {"ok": False, "error": str(exc), "code": exc.code}
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)[:300], "code": _code_of(str(exc))}

    # ---- the plugins with all the power that are on and approved, with their code checked against the approval
    def full_handover(self) -> list[dict[str, Any]]:
        """For the page: each full plugin that is on and approved, re-read now, its fingerprint checked again, its code
        (and its style sheet, with its assets inside) ready to hand over. A file changed on disk after the approval
        does not run."""
        out: list[dict[str, Any]] = []
        with self.lock:
            if self.safe:
                return out  # safe mode: no full plugin is handed over
            for listed in self.plugins:
                if listed["kind"] != "full" or listed["id"] not in self.enabled:
                    continue
                fresh = read_plugin(Path(listed["dir"]))
                if not is_approved(fresh, self.approved):
                    continue  # the code changed since the approval: it stops until the user turns it on again
                try:
                    item: dict[str, Any] = {"id": fresh["id"], "name": fresh["name"], "hash": fresh["hash"]}
                    if fresh["files"].get("page"):
                        item["code"] = Path(fresh["files"]["page"]).read_text(encoding="utf-8")
                    if fresh["files"].get("css"):
                        done = inline_assets(Path(fresh["files"]["css"]).read_text(encoding="utf-8"), Path(fresh["dir"]))
                        if done["problems"]:
                            raise ValueError(done["problems"][0])
                        item["css"] = done["css"]
                    if fingerprint(Path(fresh["dir"])) != fresh["hash"]:
                        raise ValueError("changed while it was being read")
                    out.append(item)
                except (OSError, ValueError):
                    continue
        return out

    # ---- the servers whose players the running plugins may show (the page widens its frame-src with these)
    def embed_hosts(self) -> list[str]:
        with self.lock:
            hosts: list[str] = []
            for p in self.plugins:
                if p["kind"] != "ui" or not self.is_on(p):
                    continue
                for raw in self.effective(p):
                    parsed = parse_permission(raw)
                    if parsed.get("embed") and parsed["host"] not in hosts:
                        hosts.append(parsed["host"])
            return hosts

    # ---- the list the page's host mounts (uiPlugins() of desktop/main.js)
    def ui_plugins(self) -> list[dict[str, Any]]:
        with self.lock:
            out = []
            for p in self.plugins:
                if p["kind"] != "ui" or not self.is_on(p):
                    continue
                allowed = self.effective(p)
                sources = []
                for s in p.get("sources", []):
                    embed = urllib.parse.urlsplit(re.sub(r"\{[^}]*\}", "x", s["embed"]))
                    if f"embed:{embed.hostname}" in allowed:
                        sources.append(s)
                out.append({
                    "id": p["id"], "name": p["name"], "version": p["version"], "label": p.get("label", p["id"]),
                    "url": f"/plugin/{p['id']}/", "panel": p.get("panel"), "permissions": allowed,
                    "denied": self.denied.get(p["id"], []), "rules": p.get("rules", []),
                    "themes": p.get("themes", []), "sources": sources, "activation": p.get("activation", []),
                })
            return out

    def close(self) -> None:
        self.storage.flush()
