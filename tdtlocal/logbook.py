"""The log of the browser mode: what is happening (start, plugins, confirmations, errors of the page), kept in
memory and in a file under ``<data>/browser/log.txt``, so the user can read it in Ajustes ▸ Registro and send it when
something fails. This is ``desktop/lib/logbook.js`` translated: the same scrubbing (keys and tokens that travel in an
address or a header never reach a file the user is going to send), the same line format and the same limits, so the
Registro tab shows the same in the app and in the browser (docs/PARIDAD-NAVEGADOR.md, phase 6).
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LEVELS = ["INFO", "WARN", "ERROR"]
MAX_LINE = 600
MAX_KEPT = 2000  # lines kept in memory (the file has the rest)
MAX_BYTES = 1_000_000  # when the file passes this, it is rotated to ``log.txt.1``

# keys and tokens that travel in an address or a header must not end up in a file the user is going to send
SECRET = re.compile(r"\b(token|auth|authorization|password|passwd|secret|api[_-]?key|apikey|sid|jwt|bearer)(=|%3D|:\s*)([^&\s\"']{3,})", re.IGNORECASE)
# a header that carries a credential is hidden whole («Authorization: Bearer xyz», cookies); a bare «Bearer xyz» too
HEADER = re.compile(r"\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key)(:\s*)[^\n]*", re.IGNORECASE)
BEARER = re.compile(r"\b(Bearer|Basic)\s+[\w.~+/=-]{6,}")


def scrub(text: Any) -> str:
    """Hide credentials of a line: whole headers that carry them, bare bearer tokens, and ``key=value`` pairs."""
    return HEADER.sub(r"\1\2[oculto]", BEARER.sub(r"\1 [oculto]", SECRET.sub(r"\1\2[oculto]", str(text))))


def level_of(text: str) -> str:
    """``INFO``, ``WARN`` or ``ERROR`` for a line that comes without a level (the output of the server)."""
    if re.search(r"Traceback|\bError\b|Exception|CRITICAL|\bFATAL\b", text, re.IGNORECASE):
        return "ERROR"
    if re.search(r"\bAVISO\b|\bWARN(ING)?\b", text, re.IGNORECASE):
        return "WARN"
    return "INFO"


class Logbook:
    """In-memory ring plus the file, like the desktop's. A full or protected disk never stops the app: the log stays in memory."""

    def __init__(self, file: Path | str = "", max: int = MAX_KEPT, max_bytes: int = MAX_BYTES,
                 now: Callable[[], datetime] | None = None) -> None:
        self.max = max
        self.file = Path(file) if file else None
        self.max_bytes = max_bytes
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.kept: list[str] = []
        self.file_ok = bool(file)
        self._lock = threading.Lock()

    def add(self, source: str, level: str, text: Any) -> list[str]:
        """Add one line per line of ``text`` (scrubbed and cut). Returns the lines it wrote."""
        lvl = level if level in LEVELS else "INFO"
        stamp = self.now().isoformat()[:19].replace("T", " ")
        out: list[str] = []
        for raw in re.split(r"\r?\n", str(text)):
            line = scrub(raw).rstrip()
            if not line:
                continue
            out.append(f"{stamp} {lvl:<5} [{source}] {line[:MAX_LINE] + '…' if len(line) > MAX_LINE else line}")
        with self._lock:
            self.kept.extend(out)
            if len(self.kept) > self.max:
                del self.kept[: len(self.kept) - self.max]
        self._write(out)
        return out

    def _write(self, lines: list[str]) -> None:
        if not self.file_ok or not lines or self.file is None:
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.file.stat().st_size > self.max_bytes:
                    self.file.rename(self.file.with_name(self.file.name + ".1"))
            except OSError:
                pass  # there is no file yet
            with self.file.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            self.file_ok = False

    def lines(self, limit: int = MAX_KEPT) -> list[str]:
        """The last ``limit`` lines."""
        with self._lock:
            return self.kept[-limit:] if limit else list(self.kept)

    def text(self, limit: int = MAX_KEPT) -> str:
        return "\n".join(self.lines(limit))


def lines_with_tag(path: Path | str, tag: str, limit: int) -> list[str]:
    """The last ``limit`` lines of the file that contain ``tag`` (what a plugin asks about itself, e.g. ``[plugin:mi-id]``)."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    kept = [line for line in raw if tag in line]
    return kept[-limit:] if limit else kept
