"""Phase 4 of the browser parity: development plugins (tdtlocal/devplugins.py), safe mode (tdtlocal/safemode.py) and their routes."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tdtlocal import safemode
from tdtlocal.app import App
from tdtlocal.config import Config
from tdtlocal.devplugins import DevStore, dev_signature, read_dev_plugin
from tdtlocal import plugins as plugins_mod

from .helpers import ServerCase
from .test_plugins import write_ui_plugin, write_full_plugin


def write_dev_plugin(root: Path, pid: str = "mi-dev", dev: dict | None = None) -> Path:
    folder = root / pid
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "Mi dev", "version": "0.1.0", "tdt": {"api": 0, "dev": dev or {"page": "page.js"}}}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "page.js").write_text("tdt.on('ad.started', () => tdt.log('pausa'));\n", encoding="utf-8")
    return folder


class SafeModeTests(unittest.TestCase):
    def test_decide(self) -> None:
        self.assertEqual(safemode.decide()["safe"], False)
        self.assertEqual(safemode.decide(flag=True)["why"], "flag")
        self.assertEqual(safemode.decide(env={"TDT_SAFE_MODE": "1"})["why"], "env")
        self.assertEqual(safemode.decide(forced=True)["why"], "forced")
        self.assertEqual(safemode.decide(unclean=2)["why"], "crashes")
        self.assertEqual(safemode.decide(unclean=1)["safe"], False)

    def test_crash_guard_counts_bad_exits_until_a_clean_one(self) -> None:
        with TemporaryDirectory() as tmp:
            guard = safemode.CrashGuard(Path(tmp) / "browser" / "run.json")
            self.assertEqual(guard.start(), 0, "first start is clean")
            self.assertEqual(guard.start(), 1, "the previous run did not end properly")
            self.assertEqual(guard.start(), 2)
            guard.clean_exit()
            self.assertEqual(guard.start(), 0, "a clean exit resets the count")

    def test_two_bad_exits_in_a_row_start_the_next_one_in_safe_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            guard = safemode.CrashGuard(data / "browser" / "run.json")
            guard.start()  # run 1 starts and ends badly (nobody calls clean_exit)
            guard.start()  # run 2 also ends badly
            self.assertEqual(guard.peek(), 1)
            guard.start()  # this third start finds two bad exits in a row
            self.assertEqual(guard.peek(), 2)
            app = App(Config(data_dir=data, models_dir=data / "models", port=0, allow_private=True, locked=frozenset()))
            self.addCleanup(app.close)
            self.assertTrue(app.safe_mode["safe"])
            self.assertEqual(app.safe_mode["why"], "crashes")
            self.assertIn("cerró mal", app.plugins.snapshot()["safeMode"])
            app.close()
            app2 = App(Config(data_dir=data, models_dir=data / "models", port=0, allow_private=True, locked=frozenset()))
            self.addCleanup(app2.close)
            self.assertFalse(app2.safe_mode["safe"], "a clean close means the next start is normal")

    def test_forced_file_and_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            data = Path(tmp)
            self.assertFalse(safemode.read_forced(data))
            safemode.write_forced(data, True)
            self.assertTrue(safemode.read_forced(data))
            self.assertEqual(safemode.decide(forced=safemode.read_forced(data))["why"], "forced")


class DevPluginReadTests(unittest.TestCase):
    def test_page_scope_is_read(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_dev_plugin(Path(tmp))
            p = read_dev_plugin(folder)
            self.assertEqual(p["scopes"], ["page"])
            self.assertEqual(p["problems"], [])
            self.assertEqual(dev_signature(p), f"{p['id']}|page")

    def test_main_scope_is_refused_with_the_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_dev_plugin(Path(tmp), "dev-main", dev={"main": "main.js"})
            (folder / "main.js").write_text("x", encoding="utf-8")
            p = read_dev_plugin(folder)
            self.assertTrue(any("main" in x and "navegador" in x for x in p["problems"]), p["problems"])
            self.assertEqual(p["scopes"], [])

    def test_api_must_be_0_and_files_must_exist(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "roto"
            folder.mkdir()
            (folder / "manifest.json").write_text(json.dumps({"tdt": {"api": 1, "dev": {"page": "page.js"}}}), encoding="utf-8")
            p = read_dev_plugin(folder)
            self.assertTrue(any("api" in x for x in p["problems"]))
            self.assertTrue(any("no existe" in x for x in p["problems"]))


class DevStoreTests(unittest.TestCase):
    def make_store(self, tmp: Path) -> DevStore:
        return DevStore(Config(data_dir=tmp / "data", models_dir=tmp / "models", port=0, allow_private=True, locked=frozenset()))

    def test_mode_approve_and_hot_reload_hash(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            write_dev_plugin(store.dir)
            store.scan()
            self.assertEqual(store.snapshot()["mode"], False)
            self.assertFalse(store.approve("mi-dev", True)["ok"], "sin modo desarrollador no se aprueba")
            self.assertTrue(store.set_mode(True)["ok"])
            view = store.approve("mi-dev", True)["view"]
            item = next(p for p in view["plugins"] if p["id"] == "mi-dev")
            self.assertTrue(item["approved"] and item["running"])
            self.assertIn("ad.started", item["preview"])
            first = store.page_plugins()
            self.assertEqual(first[0]["id"], "mi-dev")
            self.assertEqual(len(first[0]["hash"]), 64)
            with (store.dir / "mi-dev" / "page.js").open("a", encoding="utf-8") as fh:
                fh.write("// editado en caliente\n")
            store.scan()
            self.assertNotEqual(store.page_plugins()[0]["hash"], first[0]["hash"], "el cambio de archivo cambia la huella (hot reload)")
            status, ctype, body = store.serve_run("mi-dev")
            self.assertEqual((status, ctype), (200, "text/javascript; charset=utf-8"))
            self.assertIn("editado en caliente", body.decode("utf-8"))
            self.assertIn("'dev'", body.decode("utf-8"))
            self.assertTrue(store.approve("mi-dev", False)["ok"])
            self.assertEqual(store.page_plugins(), [])
            self.assertEqual(store.serve_run("mi-dev")[0], 404)

    def test_safe_mode_refuses_everything(self) -> None:
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            write_dev_plugin(store.dir)
            store.scan()
            store.set_mode(True)
            store.set_safe("se pidió con --safe")
            self.assertFalse(store.set_mode(False)["ok"])
            self.assertFalse(store.approve("mi-dev", True)["ok"])
            self.assertEqual(store.page_plugins(), [])
            self.assertEqual(store.serve_run("mi-dev")[0], 404)
            self.assertIn("--safe", store.snapshot()["safeMode"])


class PluginStoreSafeTests(unittest.TestCase):
    def test_safe_mode_turns_everything_off_and_refuses_changes(self) -> None:
        from tests.helpers import AppCase  # local import: AppCase is a TestCase
        # use the pieces directly instead: a store over a temp dir
        with TemporaryDirectory() as tmp:
            from tdtlocal.net import Net
            cfg = Config(data_dir=Path(tmp) / "data", models_dir=Path(tmp) / "models", port=0, allow_private=True, locked=frozenset())
            store = plugins_mod.PluginStore(cfg, Net(allow_private=True))
            write_ui_plugin(store.user_dir)
            store.scan()
            p = store.find("de-prueba")
            store.approve("de-prueba", plugins_mod.signature(p))
            store.enable("de-prueba", True)
            self.assertTrue(store.is_on(store.find("de-prueba")))
            store.set_safe("se pidió con --safe")
            self.assertFalse(store.is_on(store.find("de-prueba")), "en modo seguro ninguno corre")
            self.assertFalse(store.enable("de-prueba", False)["ok"])
            self.assertFalse(store.approve("de-prueba", "x")["ok"])
            self.assertFalse(store.set_permission("de-prueba", "storage", False)["ok"])
            self.assertEqual(store.serve("de-prueba", "/")[0], 404)
            self.assertEqual(store.call("de-prueba", "storage.keys", [])["code"], "E_UNAVAILABLE")
            self.assertIn("--safe", store.snapshot()["safeMode"])
            store.set_safe("")
            self.assertTrue(store.is_on(store.find("de-prueba")), "al salir del modo seguro vuelven los que estaban activados")


class DevAndSafeRouteTests(ServerCase):
    def test_dev_routes_and_runner(self) -> None:
        write_dev_plugin(self.app.dev.dir)
        self.post_json("/api/dev/rescan")
        status, view = self.get_json("/api/dev/state")
        self.assertEqual(status, 200)
        self.assertEqual(view["mode"], False)
        self.assertEqual(view["plugins"][0]["id"], "mi-dev")
        status, r = self.post_json("/api/dev/mode", {"on": True})
        self.assertTrue(r["ok"], r)
        status, r = self.post_json("/api/dev/approve", {"id": "mi-dev", "on": True})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["view"]["plugins"][0]["running"])
        status, page = self.get_json("/api/dev/page")
        self.assertEqual([p["id"] for p in page["plugins"]], ["mi-dev"])
        status, headers, body = self.request("GET", "/plugin-dev/mi-dev/run.js")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        self.assertIn("ad.started", body.decode("utf-8"))
        status, _, _ = self.request("GET", "/plugin-dev/mi-dev/run.js", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, r = self.post_json("/api/dev/approve", {"id": "mi-dev", "on": False})
        self.assertTrue(r["ok"])
        status, _, _ = self.request("GET", "/plugin-dev/mi-dev/run.js")
        self.assertEqual(status, 404)

    def test_safe_restart_route(self) -> None:
        write_ui_plugin(self.app.plugins.user_dir, "en-safe")
        self.app.plugins.scan()
        p = self.app.plugins.find("en-safe")
        self.app.plugins.approve("en-safe", plugins_mod.signature(p))
        self.app.plugins.enable("en-safe", True)
        status, headers, _ = self.request("GET", "/plugin/en-safe/")
        self.assertEqual(status, 200)
        status, r = self.post_json("/api/safe/restart", {"safe": True})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["safe"])
        self.assertIn("Ajustes", r["list"]["safeMode"])
        status, headers, _ = self.request("GET", "/plugin/en-safe/")
        self.assertEqual(status, 404, "en modo seguro no se sirve ningún plugin")
        status, r = self.post_json("/api/plugins/enable", {"id": "en-safe", "on": False})
        self.assertFalse(r["ok"], "en modo seguro no montar ni desmontar")
        status, r = self.post_json("/api/safe/restart", {"safe": False})
        self.assertTrue(r["ok"])
        status, headers, _ = self.request("GET", "/plugin/en-safe/")
        self.assertEqual(status, 200, "al salir del modo seguro vuelven los activados")


if __name__ == "__main__":
    unittest.main()
