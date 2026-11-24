"""Reading and writing the files of a plugin when an AI asks for it through the MCP (``desktop/lib/plugin-writer.js`` and the
write half of ``desktop/lib/dev-admin.js``, translated; docs/PARIDAD-NAVEGADOR.md, phase 5).

The rules, in the order they matter:

1. The command was already cleaned by ``control.py`` when it entered the bus; here everything is checked AGAIN, because the
   page that forwards the command is code too.
2. A write is first staged in a folder apart and read as a plugin (manifest and all), so a mistake comes back to the AI
   without bothering the user; ``dry_run`` stops there.
3. Only a write that passes is shown to the user, with the code, and only if they accept does anything touch the plugin's
   folder (the page asks; the server applies when the page says the user accepted).
"""

from __future__ import annotations

import os
import shutil
import stat as stat_mod
import tempfile
from pathlib import Path
from typing import Any

from .control import DEV_EXTENSIONS, DEV_FILE, DEV_MAX_FILE_BYTES, DEV_MAX_FILES, DEV_MAX_TOTAL_BYTES, DEV_MAX_NAME
from .plugins import inside_of, read_plugin

LIMITS = {"files": DEV_MAX_FILES, "file": DEV_MAX_FILE_BYTES, "total": DEV_MAX_TOTAL_BYTES, "listed": 24}
SCOPE_TEXT = {"page": "correr dentro de la página de TDT local, con acceso a todo lo que ve y hace"}


def check_files(files: Any) -> str | None:
    """A sentence about what is wrong with these files, or None. The same rules as ``control.py``, checked again here."""
    if not isinstance(files, dict) or not 1 <= len(files) <= LIMITS["files"]:
        return f"hacen falta de 1 a {LIMITS['files']} archivos"
    total = 0
    for name, content in files.items():
        if not isinstance(name, str) or len(name) > DEV_MAX_NAME or not DEV_FILE.match(name):
            return f"nombre de archivo no válido: «{str(name)[:60]}»"
        if not name.lower().endswith(DEV_EXTENSIONS):
            return f"«{name}»: solo se admiten archivos {', '.join(DEV_EXTENSIONS)}"
        if content is not None and not isinstance(content, str):
            return f"«{name}» tiene que ser texto (o null para borrarlo)"
        size = len(content.encode("utf-8")) if content else 0
        if size > LIMITS["file"]:
            return f"«{name}» pesa más de {LIMITS['file'] // 1024} KB"
        total += size
    return None if total <= LIMITS["total"] else f"los archivos pesan más de {LIMITS['total'] // 1024} KB en total"


def read_folder(root: Path) -> dict[str, str]:
    """The text files of a plugin folder (not hidden, not links, not big, of the allowed kinds), by relative name."""
    root = Path(root)
    out: dict[str, str] = {}

    def walk(base: Path, prefix: str, depth: int) -> None:
        if len(out) >= LIMITS["listed"]:
            return
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return
        for entry in entries:
            if len(out) >= LIMITS["listed"]:
                return
            if entry.startswith("."):
                continue
            full = base / entry
            try:
                st = os.lstat(full)
            except OSError:
                continue
            name = prefix + entry
            if stat_mod.S_ISDIR(st.st_mode):
                if depth < 2:
                    walk(full, name + "/", depth + 1)
            elif stat_mod.S_ISREG(st.st_mode) and not stat_mod.S_ISLNK(st.st_mode) and st.st_size <= LIMITS["file"] and name.lower().endswith(DEV_EXTENSIONS):
                try:
                    out[name] = full.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

    walk(root, "", 0)
    return out


def diff_files(existing: dict[str, str], finals: dict[str, str | None]) -> list[dict[str, Any]]:
    """What changes: every file the write touches, as `nuevo`, `cambiado`, `borrado` or `igual` (against the folder now)."""
    changes = []
    for name in sorted(finals):
        before = existing.get(name)
        after = finals[name]
        if before is None and after is None:
            continue
        status = "nuevo" if before is None else "borrado" if after is None else "cambiado" if before != after else "igual"
        changes.append({"name": name, "status": status, "bytes": len(after.encode("utf-8")) if after else 0})
    return changes


def stage(files: dict[str, str | None], base: Path | None, plugin_id: str) -> Path:
    """Put the files on a copy of the plugin's folder (or a fresh one) in a temporary place, and return the staged folder."""
    stage_dir = Path(tempfile.mkdtemp(prefix="tdt-write-"))
    target = stage_dir / plugin_id
    if base is not None and Path(base).is_dir():
        shutil.copytree(base, target)
    else:
        target.mkdir(parents=True)
    for name, content in files.items():
        dest = target.joinpath(*name.split("/"))
        if content is None:
            if dest.is_file():
                dest.unlink()
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return target


def apply_files(files: dict[str, str | None], target: Path) -> None:
    """Write the accepted files into the plugin's real folder. The names were already validated by control.py; here the
    resolved path is checked again so nothing can leave the folder (``..``, absolute paths, links)."""
    target = Path(target).resolve()
    for name, content in files.items():
        parts = [p for p in str(name).replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or any(p == ".." or p.startswith(".") for p in parts):
            raise ValueError(f"«{name}» no es un nombre de archivo dentro de la carpeta")
        real = target.joinpath(*parts).resolve()
        if real != target and target not in real.parents:
            raise ValueError(f"«{name}» se sale de la carpeta")
        if content is None:
            if real.is_file():
                real.unlink()
            continue
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(content, encoding="utf-8")


def describe_write(name: str, changes: list[dict[str, Any]], finals: dict[str, str | None], active: bool, scopes: list[str]) -> dict[str, str]:
    """The question to the user about a write: what changes, what the plugin can do, and the start of the new code."""
    lines = [f"  • {c['name']}: {c['status']}" + ("" if c["status"] == "borrado" else f" ({c['bytes']} bytes)") for c in changes if c["status"] != "igual"]
    shown = 0
    code = []
    for c in changes:
        if c["status"] in ("igual", "borrado") or shown >= 3200:
            continue
        text = finals.get(c["name"]) or ""
        room = min(1000, 3200 - shown)
        code.append(f"── {c['name']} ──\n{text[:room]}" + (f"\n… ({len(text) - room} caracteres más)" if len(text) > room else ""))
        shown += min(len(text), room)
    return {
        "message": f"Una IA quiere {'cambiar' if active else 'escribir'} el plugin «{name}»",
        "detail": (
            ("ESTÁ ACTIVADO: el código nuevo se ejecutará en cuanto aceptes.\n\n" if active else "No correrá hasta que lo actives tú.\n\n")
            + (f"Cambios:\n{chr(10).join(lines)}\n\n" if lines else "Sin cambios en los archivos actuales.\n\n")
            + "Cuando corra podrá:\n" + "\n".join(f"  • {SCOPE_TEXT.get(s, s)}" for s in scopes)
            + ("\n\nEl código nuevo:\n\n" + "\n\n".join(code) if code else "")
        ),
    }


def check_staged(staged: Path) -> dict[str, Any]:
    """Read the staged folder as a plugin: its manifest problems are what the AI gets back before any question."""
    plugin = read_plugin(staged)
    return {"id": plugin["id"], "kind": plugin["kind"], "problems": plugin["problems"], "plugin": plugin}
