"""The page's preferences on the server (``<data>/prefs.json``, GET/POST /api/prefs): so favorites and the rest survive
a change of origin in browser mode (port, --lan, an installed PWA) — ROADMAP.md. Phase 10 of
docs/PARIDAD-NAVEGADOR.md."""

from __future__ import annotations

import json
import unittest

from .helpers import ServerCase


class PrefsRouteTests(ServerCase):
    def test_get_starts_empty_and_post_sets_one_key(self) -> None:
        status, r = self.get_json("/api/prefs")
        self.assertEqual(status, 200)
        self.assertEqual(r["prefs"], {})
        status, r = self.post_json("/api/prefs", {"key": "favs", "value": ["la-1", "24h"]})
        self.assertEqual(status, 200)
        self.assertTrue(r["ok"], r)
        status, r = self.get_json("/api/prefs")
        self.assertEqual(r["prefs"]["favs"], ["la-1", "24h"])
        # and it is in the file, valid JSON (atomic write)
        data = json.loads((self.app.config.data_dir / "prefs.json").read_text(encoding="utf-8"))
        self.assertEqual(data["favs"], ["la-1", "24h"])

    def test_bad_keys_and_big_values_are_refused(self) -> None:
        for key in ("Favs", "../x", "", "a" * 30, 42):
            status, r = self.post_json("/api/prefs", {"key": key, "value": 1})
            self.assertFalse(r["ok"], (key, r))
        status, r = self.post_json("/api/prefs", {"key": "vol", "value": "x" * 5000})
        self.assertFalse(r["ok"])
        self.assertIn("grande", r["error"])
        status, r = self.post_json("/api/prefs", {"key": "vol", "value": {"v": 0.5, "m": False}})
        self.assertTrue(r["ok"], r)

    def test_snapshot_is_a_copy(self) -> None:
        self.post_json("/api/prefs", {"key": "panel", "value": True})
        snap = self.app.prefs.snapshot()
        snap["panel"] = False
        self.assertTrue(self.app.prefs.snapshot()["panel"])


if __name__ == "__main__":
    unittest.main()
