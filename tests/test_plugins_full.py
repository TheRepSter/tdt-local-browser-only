"""The runner module of the full plugins over HTTP: ``/plugin-full/<id>/run.js`` (the rest of the plugin server is in test_plugins.py)."""

from __future__ import annotations

import unittest
from pathlib import Path

from tdtlocal import plugins

from .helpers import ServerCase
from .test_plugins import write_full_plugin, write_ui_plugin


class FullRunRouteTests(ServerCase):
    """The module the page imports to run a full plugin, served only while the fingerprint matches the approval."""

    def make_full(self, pid: str, enable: bool = True) -> Path:
        folder = write_full_plugin(self.app.plugins.user_dir, pid)
        self.app.plugins.scan()
        p = self.app.plugins.find(pid)
        self.app.plugins.approve(pid, plugins.full_signature(p))
        if enable:
            self.app.plugins.enable(pid, True)
        return folder

    def test_the_runner_is_served_with_its_code_and_stops_when_the_fingerprint_changes(self) -> None:
        pid = "full-http"
        folder = self.make_full(pid)
        status, headers, body = self.request("GET", f"/plugin-full/{pid}/run.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/javascript; charset=utf-8")
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        text = body.decode("utf-8")
        self.assertIn("ad.started", text)
        self.assertIn(f'window.tdtPluginHost.load("{pid}", run, \'full\')', text)
        self.assertIn("async (tdt) => {", text)
        with (folder / "page.js").open("a", encoding="utf-8") as fh:  # una letra basta
            fh.write("// cambiada\n")
        self.app.plugins.scan()
        status, _, body = self.request("GET", f"/plugin-full/{pid}/run.js")
        self.assertEqual(status, 409, "la huella cambió: no se sirve código")
        self.assertIn("cambió", body.decode("utf-8"))

    def test_not_enabled_or_unknown_is_404_and_cross_site_is_403(self) -> None:
        self.make_full("full-apagado", enable=False)
        status, _, _ = self.request("GET", "/plugin-full/full-apagado/run.js")
        self.assertEqual(status, 404, "aprobado pero apagado no se sirve")
        status, _, _ = self.request("GET", "/plugin-full/nadie/run.js")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/plugin-full/nadie/run.js", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        write_ui_plugin(self.app.plugins.user_dir, "ui-http")
        self.app.plugins.scan()
        p = self.app.plugins.find("ui-http")
        self.app.plugins.approve("ui-http", plugins.signature(p))
        self.app.plugins.enable("ui-http", True)
        status, _, _ = self.request("GET", "/plugin-full/ui-http/run.js")
        self.assertEqual(status, 404, "un plugin con permisos no tiene runner")


if __name__ == "__main__":
    unittest.main()
