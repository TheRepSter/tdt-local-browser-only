"""Safe mode of the browser mode (``desktop/lib/safe-mode.js`` translated; docs/PARIDAD-NAVEGADOR.md, phase 4).

The server starts without running any plugin, so a plugin that breaks it cannot keep doing so. It is entered by hand
(``--safe``, ``TDT_SAFE_MODE=1``, «Reiniciar en modo seguro» in the settings, or a file the settings wrote), or by itself
when the server has died twice in a row without closing properly. Nothing the user chose is lost: what was on stays in
its files and runs again when the safe mode is left. Pure functions plus a small marker file in ``<data>/browser/``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CRASHES_FOR_SAFE_MODE = 2

WHY = {
    "flag": "se pidió con --safe",
    "env": "se pidió con TDT_SAFE_MODE=1",
    "crashes": "el servidor se cerró mal las dos últimas veces que arrancó",
    "forced": "está puesto desde Ajustes ▸ Plugins (se saca de allí)",
}


def decide(flag: bool = False, env: dict[str, str] | None = None, unclean: int = 0,
           forced: bool = False, threshold: int = CRASHES_FOR_SAFE_MODE) -> dict[str, Any]:
    """Should this start be in safe mode? ``{safe, why, text}``."""
    env = os.environ if env is None else env
    if flag:
        why = "flag"
    elif env.get("TDT_SAFE_MODE") == "1":
        why = "env"
    elif forced:
        why = "forced"
    elif unclean >= threshold:
        why = "crashes"
    else:
        why = ""
    return {"safe": bool(why), "why": why, "text": WHY.get(why, "")}


class CrashGuard:
    """The marker of how the last run ended, in ``<data>/browser/run.json``: at the start it says «running», and a clean
    exit says it is not. A start that finds «running» is a start after a run that did not end properly."""

    def __init__(self, file: Path) -> None:
        self.file = Path(file)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.file)

    def peek(self) -> int:
        """How many starts in a row ended badly, without marking anything (for deciding before the server starts)."""
        was = self._read()
        if was.get("running") is not True:
            return 0
        return was.get("unclean") if isinstance(was.get("unclean"), int) and was["unclean"] >= 0 else 0

    def start(self) -> int:
        """Call at the start: how many starts in a row ended badly, counting the last one. Marks this run as «running»."""
        unclean = self.peek() + 1 if self._read().get("running") is True else 0
        self._write({"running": True, "unclean": unclean})
        return unclean

    def clean_exit(self) -> None:
        """Call when the server closes properly (or when the user leaves the safe mode): the count starts again."""
        self._write({"running": False, "unclean": 0})


def forced_file(data_dir: Path) -> Path:
    """The file «Reiniciar en modo seguro» writes (the browser cannot relaunch the process with another flag)."""
    return Path(data_dir) / "browser" / "safe.json"


def read_forced(data_dir: Path) -> bool:
    try:
        return json.loads(forced_file(data_dir).read_text(encoding="utf-8")).get("safe") is True
    except (OSError, ValueError, AttributeError):
        return False


def write_forced(data_dir: Path, safe: bool) -> None:
    file = forced_file(data_dir)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"safe": bool(safe)}), encoding="utf-8")
    os.replace(tmp, file)


def argv_wants_safe(argv: list[str] | None = None) -> bool:
    return "--safe" in (sys.argv[1:] if argv is None else argv)
