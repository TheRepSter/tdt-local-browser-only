"""The log of the browser mode: the scrubbing and the file (tdtlocal/logbook.py, a port of desktop/lib/logbook.js)
and the routes that feed and serve it (POST /api/log, GET /api/logs) — docs/PARIDAD-NAVEGADOR.md, phase 6."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tdtlocal.logbook import LEVELS, Logbook, level_of, lines_with_tag, scrub

from .helpers import ServerCase


class ScrubTests(unittest.TestCase):
    def test_hides_credentials_in_addresses_and_headers(self) -> None:
        self.assertIn("token=[oculto]", scrub("falló https://x.test/?token=abc123456&lang=es"))
        self.assertIn("api_key: [oculto]", scrub("api_key: xyz987654"))
        self.assertIn("Authorization: [oculto]", scrub("Authorization: Bearer abc.def.ghi"))
        self.assertIn("Cookie: [oculto]", scrub("Cookie: sid=abcdef; other=1"))
        self.assertIn("Bearer [oculto]", scrub("cabecera suelta: Bearer abcdef123456"))
        # short values are left alone (a 3-character «token» is probably a word, not a secret)
        self.assertEqual(scrub("token=ab"), "token=ab")
        # what is not a credential stays
        self.assertIn("lang=es", scrub("https://x.test/?token=abc123456&lang=es"))

    def test_level_of(self) -> None:
        self.assertEqual(level_of("Traceback (most recent call last): ..."), "ERROR")
        self.assertEqual(level_of("AVISO: accesible desde la red local"), "WARN")
        self.assertEqual(level_of("todo bien"), "INFO")


class LogbookTests(unittest.TestCase):
    def test_add_formats_scrubs_and_cuts(self) -> None:
        book = Logbook(now=lambda: datetime(2026, 11, 1, 19, 45, 7, tzinfo=timezone.utc))
        out = book.add("plugins", "WARN", "token=abcdef123456\nlinea dos\n\n")
        self.assertEqual(out, [
            "2026-11-01 19:45:07 WARN  [plugins] token=[oculto]",
            "2026-11-01 19:45:07 WARN  [plugins] linea dos",
        ])
        self.assertEqual(book.lines(), out)
        long = book.add("servidor", "INFO", "x" * 700)
        self.assertTrue(long[0].endswith("…") and len(long[0]) < 700)
        self.assertIn("INFO", LEVELS)

    def test_writes_and_rotates_the_file(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "browser" / "log.txt"
            book = Logbook(path, max_bytes=200, now=lambda: datetime(2026, 11, 1, tzinfo=timezone.utc))
            for i in range(10):
                book.add("servidor", "INFO", f"linea {i} " + "y" * 40)
            self.assertTrue(path.exists())
            self.assertTrue(Path(str(path) + ".1").exists())  # it rotated once past max_bytes
            self.assertIn("[servidor] linea 9", path.read_text(encoding="utf-8"))
            self.assertEqual(len(book.lines()), 10)  # the memory ring is independent of the file
            self.assertEqual(lines_with_tag(path, "[servidor] linea 9", 5)[-1].endswith("linea 9 " + "y" * 40), True)
            self.assertEqual(lines_with_tag(path, "[otro]", 5), [])

    def test_ring_keeps_the_last_max(self) -> None:
        book = Logbook(max=5)
        for i in range(8):
            book.add("s", "INFO", f"n{i}")
        self.assertEqual([l.split("] ")[1] for l in book.lines()], [f"n{i}" for i in range(3, 8)])
        self.assertEqual(len(book.lines(2)), 2)

    def test_a_protected_disk_does_not_raise(self) -> None:
        book = Logbook(Path("Z:/no/existe/esta/ruta/log.txt") if Path("Z:/").drive else Path("/proc/1/log.txt"))
        book.add("s", "INFO", "Igual se guarda en memoria")
        self.assertEqual(len(book.lines()), 1)
        self.assertFalse(book.file_ok)


class LogRouteTests(ServerCase):
    def test_post_and_get_scrubbed(self) -> None:
        status, r = self.post_json("/api/log", {"source": "página", "level": "ERROR",
                                                "text": "falló con token=abcdef123456 en https://x.test/"})
        self.assertEqual(status, 200)
        self.assertTrue(r["ok"])
        status, r = self.get_json("/api/logs")
        self.assertEqual(status, 200)
        joined = "\n".join(r["lines"])
        self.assertIn("[página]", joined)
        self.assertIn("token=[oculto]", joined)
        self.assertNotIn("abcdef123456", joined)
        self.assertTrue(r["folder"].endswith("browser"))
        self.assertEqual(r["file"], "log.txt")
        # and it is in the file too, so a restart keeps it
        file_lines = (self.app.config.data_dir / "browser" / "log.txt").read_text(encoding="utf-8")
        self.assertIn("token=[oculto]", file_lines)

    def test_plugin_source_builds_the_tag(self) -> None:
        status, r = self.post_json("/api/log", {"source": "plugin", "id": "mi-plugin", "level": "INFO", "text": "hola"})
        self.assertTrue(r["ok"], r)
        status, r = self.get_json("/api/logs")
        self.assertIn("[plugin:mi-plugin] hola", "\n".join(r["lines"]))
        # the admin's plugin_logs reads the same file
        from tdtlocal.pluginadmin import _log_lines
        self.assertTrue(any("[plugin:mi-plugin] hola" in l for l in _log_lines(self.app.config.data_dir, "[plugin:mi-plugin]", 50)))

    def test_bad_bodies_are_refused(self) -> None:
        for body in ({"source": "Plugin:X", "text": "x"}, {"source": "página"}, {"source": "página", "text": "  "},
                     {"source": "plugin", "id": "../etc", "text": "x"}, {"text": "x"}):
            status, r = self.post_json("/api/log", body)
            self.assertEqual(status, 400, (body, r))
            self.assertFalse(r["ok"])
        # an unknown level is coerced to INFO, not refused
        status, r = self.post_json("/api/log", {"source": "página", "level": "CHILL", "text": "tranqui"})
        self.assertTrue(r["ok"])
        _, got = self.get_json("/api/logs")
        self.assertIn("INFO  [página] tranqui", "\n".join(got["lines"]))

    def test_the_start_is_logged(self) -> None:
        self.app.start_background()
        status, r = self.get_json("/api/logs")
        joined = "\n".join(r["lines"])
        self.assertIn("[servidor]", joined)
        self.assertIn("encontrados; activados:", joined)


if __name__ == "__main__":
    unittest.main()
