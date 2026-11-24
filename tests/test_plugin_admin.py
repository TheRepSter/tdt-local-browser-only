"""Phase 5 of the browser parity: the MCP admin commands about plugins and dev plugins (tdtlocal/pluginadmin.py, the
/api/*/admin routes) and one command going around the control bus and waiting for the page's answer."""

from __future__ import annotations

import http.client
import json
import queue
import threading
import unittest

from .helpers import ServerCase
from .test_dev_safemode import write_dev_plugin
from .test_plugins import write_ui_plugin


class PluginAdminRouteTests(ServerCase):
    config_overrides = {"control_enabled": True, "control_level": "dev"}

    def test_list_read_permission_config_and_the_confirm_flow(self) -> None:
        write_ui_plugin(self.app.plugins.user_dir, "admin-ui", settings={"tema": {"type": "choice", "label": "Tema", "options": ["claro", "oscuro"], "default": "claro"}})
        self.app.plugins.scan()
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_list"}})
        self.assertTrue(r["ok"])
        self.assertIn("admin-ui", [p["id"] for p in r["data"]["plugins"]])
        # enable without confirm: needsConfirm with the review, and nothing is on yet
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_enable", "id": "admin-ui"}})
        self.assertFalse(r["ok"])
        self.assertTrue(r["needsConfirm"])
        self.assertIn("review", r["ask"])
        self.assertFalse(self.app.plugins.is_on(self.app.plugins.find("admin-ui")))
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_enable", "id": "admin-ui"}, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.app.plugins.is_on(self.app.plugins.find("admin-ui")))
        # disable needs no question, and enabling again an already approved plugin asks nothing either
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_disable", "id": "admin-ui"}})
        self.assertTrue(r["ok"])
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_enable", "id": "admin-ui"}})
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.app.plugins.is_on(self.app.plugins.find("admin-ui")))
        # a permission back asks; taking it away does not
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_permission", "id": "admin-ui", "permission": "storage", "on": False}})
        self.assertTrue(r["ok"], r)
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_permission", "id": "admin-ui", "permission": "storage", "on": True}})
        self.assertTrue(r["needsConfirm"])
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_permission", "id": "admin-ui", "permission": "storage", "on": True}, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertIn("storage", self.app.plugins.effective(self.app.plugins.find("admin-ui")))
        # config asks too
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_config", "id": "admin-ui", "values": {"tema": "oscuro"}}})
        self.assertTrue(r["needsConfirm"])
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_config", "id": "admin-ui", "values": {"tema": "oscuro"}}, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.app.plugins.call("admin-ui", "config.get", ["tema"])["result"], "oscuro")
        # read shows the files
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_read", "id": "admin-ui"}})
        self.assertTrue(r["ok"])
        self.assertIn("app.js", r["data"]["files"])

    def test_write_dry_run_needs_confirm_and_then_writes(self) -> None:
        write_ui_plugin(self.app.plugins.user_dir, "admin-write")
        self.app.plugins.scan()
        files = {"app.js": "console.log('nuevo');\n", "nuevo.txt": "hola\n"}
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_write", "id": "admin-write", "files": files, "dry_run": True}})
        self.assertTrue(r["ok"], r)
        self.assertFalse((self.app.plugins.user_dir / "admin-write" / "nuevo.txt").exists(), "dry_run no escribe")
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_write", "id": "admin-write", "files": files}})
        self.assertTrue(r["needsConfirm"])
        self.assertIn("app.js", r["ask"]["detail"])
        self.assertFalse((self.app.plugins.user_dir / "admin-write" / "nuevo.txt").exists())
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_write", "id": "admin-write", "files": files}, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual((self.app.plugins.user_dir / "admin-write" / "nuevo.txt").read_text(encoding="utf-8"), "hola\n")
        self.assertIn("nuevo", (self.app.plugins.user_dir / "admin-write" / "app.js").read_text(encoding="utf-8"))

    def test_bad_writes_are_refused_and_bundled_plugins_are_not_writable(self) -> None:
        write_ui_plugin(self.app.plugins.user_dir, "admin-bad")
        self.app.plugins.scan()
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_write", "id": "admin-bad", "files": {"../evil.js": "x"}}})
        self.assertEqual(status, 400, "el limpiador del bus vuelve a rechazar la ruta saliente")
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_write", "id": "admin-bad", "files": {"manifest.json": "no es json"}}})
        self.assertFalse(r["ok"])
        self.assertIn("problemas", r["detail"])
        bundled = next(p for p in self.app.plugins.plugins if p["source"] == "incluido con la aplicación")
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "plugin_write", "id": bundled["id"], "files": {"x.txt": "x"}}})
        self.assertFalse(r["ok"], "lo incluido con la aplicación no se escribe")
        status, r = self.post_json("/api/plugins/admin", {"cmd": {"do": "no_existe"}})
        self.assertEqual(status, 400)


class DevAdminRouteTests(ServerCase):
    config_overrides = {"control_enabled": True, "control_level": "dev"}

    def test_dev_admin_flow(self) -> None:
        write_dev_plugin(self.app.dev.dir)
        self.app.dev.scan()
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_list"}})
        self.assertFalse(r["ok"], "sin modo desarrollador no se hace nada")
        self.assertIn("apagado", r["detail"])
        self.post_json("/api/dev/mode", {"on": True})
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_list"}})
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["plugins"][0]["id"], "mi-dev")
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_read", "id": "mi-dev"}})
        self.assertIn("page.js", r["data"]["files"])
        files = {"page.js": "tdt.log('cambiado');\n"}
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_write", "id": "mi-dev", "files": files, "dry_run": True}})
        self.assertTrue(r["ok"], r)
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_write", "id": "mi-dev", "files": files}})
        self.assertTrue(r.get("needsConfirm"), r)
        self.assertIn("cambiado", r["ask"]["detail"])
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_write", "id": "mi-dev", "files": files}, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertIn("cambiado", (self.app.dev.dir / "mi-dev" / "page.js").read_text(encoding="utf-8"))
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_activate", "id": "mi-dev", "on": True}})
        self.assertTrue(r["needsConfirm"])
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_activate", "id": "mi-dev", "on": True}, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertIn("mi-dev", [p["id"] for p in self.app.dev.page_plugins()])
        # a manifest without tdt.dev is refused before any question
        status, r = self.post_json("/api/dev/admin", {"cmd": {"do": "dev_write", "id": "otro", "files": {"manifest.json": json.dumps({"tdt": {"api": 1}})}, "dry_run": True}})
        self.assertFalse(r["ok"])
        self.assertIn("tdt.dev", r["detail"])


class AdminBusTests(ServerCase):
    """One command around the control bus: the client sends it, the page gets it over the event stream, asks the admin
    route (which says needsConfirm), the user accepts in the page's dialog, and the page answers the bus."""

    config_overrides = {"control_enabled": True, "control_level": "dev"}

    def auth(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.app.control.token()}

    def test_plugin_enable_goes_around_the_bus(self) -> None:
        write_ui_plugin(self.app.plugins.user_dir, "bus-ui")
        self.app.plugins.scan()
        stream = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(stream.close)
        stream.request("GET", "/api/control/events")
        response = stream.getresponse()
        response.readline()  # «conectado»
        self.addCleanup(response.close)
        results: queue.Queue = queue.Queue()

        def client() -> None:
            status, _, raw = self.request("POST", "/api/control/v1/command", {"do": "plugin_enable", "id": "bus-ui"}, self.auth())
            results.put((status, json.loads(raw)))

        threading.Thread(target=client, daemon=True).start()
        event = data = b""
        while not data:
            line = response.readline()
            if line.startswith(b"event:"):
                event = line
            elif line.startswith(b"data:"):
                data = line[5:]
        command = json.loads(data)
        self.assertEqual(command["cmd"], {"do": "plugin_enable", "id": "bus-ui"})
        # what the page does: admin, see needsConfirm, ask the user, admin again with confirm, answer the bus
        _, first = self.post_json("/api/plugins/admin", {"cmd": command["cmd"]})
        self.assertTrue(first["needsConfirm"], "la primera llamada espera al diálogo de la página")
        _, second = self.post_json("/api/plugins/admin", {"cmd": command["cmd"], "confirm": True})
        self.assertTrue(second["ok"], second)
        self.post_json("/api/control/result", {"id": command["id"], "ok": second["ok"], "detail": second["detail"]})
        status, body = results.get(timeout=10)
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertTrue(self.app.plugins.is_on(self.app.plugins.find("bus-ui")))


if __name__ == "__main__":
    unittest.main()
