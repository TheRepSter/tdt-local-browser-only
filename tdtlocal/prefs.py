"""The user's preferences of the page (favorites, volume, layout, rotation, panel…), kept in ``<data>/prefs.json``
so they survive a change of origin: in browser mode the origin changes with the port, ``--lan``, ``localhost``
vs ``127.0.0.1`` or an installed PWA, and ``localStorage`` is per-origin (ROADMAP.md: «Favoritos y preferencias en
el servidor»). The page keeps ``localStorage`` as its boot cache and syncs both ways on load (store.js).

Only from this machine can they be changed (like the settings). The keys are not secrets (channel ids, numbers,
small objects), the values are capped, and the file is written atomically.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

KEY = re.compile(r"^[a-z][a-z0-9]{0,20}$")
MAX_VALUE_BYTES = 4096
MAX_KEYS = 200


class PrefsStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        self.prefs: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.prefs = {k: v for k, v in data.items() if isinstance(k, str)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.prefs = {}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.prefs)

    def set(self, key: Any, value: Any) -> dict[str, Any]:
        """Set one key. Returns ``{"ok": True, …}`` or the error; never raises."""
        if not isinstance(key, str) or not KEY.match(key):
            return {"ok": False, "error": "clave no válida (letra minúscula y hasta 21 caracteres)"}
        try:
            packed = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return {"ok": False, "error": "valor no serializable"}
        if len(packed.encode("utf-8")) > MAX_VALUE_BYTES:
            return {"ok": False, "error": "valor demasiado grande (hasta 4 KB)"}
        with self.lock:
            if len(self.prefs) >= MAX_KEYS and key not in self.prefs:
                return {"ok": False, "error": "demasiadas claves"}
            self.prefs[key] = value
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(self.prefs, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self.path)  # atomic: a crash mid-write never leaves a half file
            except OSError as exc:
                return {"ok": False, "error": f"no se pudo guardar: {exc}"}
            return {"ok": True, "key": key, "prefs": dict(self.prefs)}
