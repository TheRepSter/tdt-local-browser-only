"""The installable app (PWA): the manifest and the passthrough service worker the server serves, and the page's links
to them — phase 8 of docs/PARIDAD-NAVEGADOR.md (the browser's substitute of the desktop app's own window)."""

from __future__ import annotations

import json
import unittest

from .helpers import ServerCase


class PwaRouteTests(ServerCase):
    def test_manifest_is_served_and_complete(self) -> None:
        status, headers, body = self.request("GET", "/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertIn("manifest", headers.get("content-type", ""))
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["name"], "TDT local")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")
        self.assertTrue(any(i["src"] == "/icon.svg" for i in data["icons"]))
        # the icon it points at exists
        status, _, icon = self.request("GET", "/icon.svg")
        self.assertEqual(status, 200)
        self.assertIn(b"<svg", icon)

    def test_service_worker_is_a_passthrough(self) -> None:
        status, headers, body = self.request("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("content-type", ""))
        text = body.decode("utf-8")
        self.assertIn("addEventListener('fetch'", text)
        self.assertNotIn("respondWith", text)  # nothing is cached: the Python server must be running anyway
        self.assertNotIn("caches.", text)

    def test_the_page_links_manifest_and_icon(self) -> None:
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', text)
        self.assertIn('rel="icon" href="/icon.svg"', text)
        self.assertIn('name="theme-color"', text)
        self.assertIn('id="btnInstall"', text)  # «Instalar como aplicación»
        self.assertIn('id="chkNotif"', text)  # avisos del sistema (Notification API)


if __name__ == "__main__":
    unittest.main()
