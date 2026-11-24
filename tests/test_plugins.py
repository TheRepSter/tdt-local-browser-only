"""The plugin server of the browser mode: permissions, manifests, packages, storage, serving and the HTTP routes."""

from __future__ import annotations

import http.client
import json
import os
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tdtlocal import plugins
from tdtlocal.plugins import PluginError, PluginStore
from tdtlocal import pluginsign

from .helpers import AppCase, ServerCase


def ui_manifest(permissions: list[str] | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    tdt: dict[str, Any] = {
        "api": 1,
        "entry": "index.html",
        "label": "Prueba",
        "panel": {"width": 320, "height": 200},
        "permissions": permissions if permissions is not None else ["player.read", "storage", "network:api.example.com", "embed:player.example.tv"],
    }
    if settings is not None:
        tdt["settings"] = settings
    return {
        "name": "De prueba", "version": "1.0.0", "description": "plugin de las pruebas", "author": "tests",
        "tdt": tdt,
    }


def write_ui_plugin(root: Path, pid: str = "de-prueba", **kwargs: Any) -> Path:
    folder = root / pid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(ui_manifest(**kwargs), ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "index.html").write_text(
        '<!doctype html><title>prueba</title><script src="/@tdt/sdk.js"></script><script src="app.js"></script>',
        encoding="utf-8",
    )
    (folder / "app.js").write_bytes(b"console.log('hola');\n")
    return folder


def write_full_plugin(root: Path, pid: str = "full-de-prueba", full: dict[str, Any] | None = None) -> Path:
    folder = root / pid
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "Con todo el poder", "version": "1.0.0", "tdt": {"api": 1, "full": full or {"page": "page.js"}}}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "page.js").write_text("tdt.on('ad.started', () => tdt.log('pausa'));\n", encoding="utf-8")
    return folder


class PermissionTests(unittest.TestCase):
    def test_known_permissions_are_accepted(self) -> None:
        for name in plugins.PERMISSIONS:
            self.assertEqual(plugins.parse_permission(name), {"name": name})
        self.assertEqual(plugins.risk_of("embed:x.tv"), "critical")
        self.assertEqual(plugins.risk_of("network:x.com"), "high")
        self.assertEqual(plugins.risk_of("ui.toast"), "low")

    def test_network_permission_needs_a_public_name(self) -> None:
        self.assertEqual(plugins.parse_permission("network:api.example.com")["host"], "api.example.com")
        self.assertTrue(plugins.parse_permission("network:*.example.com")["wildcard"])
        for bad in ("network:localhost", "network:127.0.0.1", "network:192.168.1.1", "network:algo.local", "network:", "network:ejemplo"):
            self.assertIn("problem", plugins.parse_permission(bad), bad)

    def test_embed_permission_needs_an_exact_public_name(self) -> None:
        self.assertEqual(plugins.parse_permission("embed:player.example.tv")["host"], "player.example.tv")
        for bad in ("embed:*.example.tv", "embed:localhost", "embed:10.0.0.2"):
            self.assertIn("problem", plugins.parse_permission(bad), bad)

    def test_network_allows_only_covers_what_it_says(self) -> None:
        perms = ["network:api.example.com", "network:*.example.org"]
        self.assertTrue(plugins.network_allows(perms, "api.example.com"))
        self.assertTrue(plugins.network_allows(perms, "cdn.example.org"))
        self.assertFalse(plugins.network_allows(perms, "xapi.example.com"), "no vale de sufijo suelto")
        self.assertFalse(plugins.network_allows(perms, "example.org"), "el comodín no cubre el dominio pelado")
        self.assertFalse(plugins.network_allows(perms, "example.com"))
        self.assertFalse(plugins.network_allows([], "api.example.com"))


class PatternTests(unittest.TestCase):
    def test_good_patterns(self) -> None:
        p = plugins.parse_pattern("https://example.com/ver/*")
        self.assertTrue(p["test"]("https://example.com/ver/123"))
        self.assertFalse(p["test"]("https://example.com/otro"))
        self.assertTrue(plugins.parse_pattern("https://*.example.com/*")["test"]("https://a.example.com/x"))

    def test_local_or_oversized_patterns_are_refused(self) -> None:
        for bad in ("https://127.0.0.1/*", "https://localhost/*", "https://[::1]/*", "https://*/x", "https://*example.com/*", "no es un patrón"):
            self.assertIn("problem", plugins.parse_pattern(bad), bad)


class FilesTests(unittest.TestCase):
    def test_fingerprint_changes_with_the_code_and_ignores_hidden_files(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.js").write_text("uno", encoding="utf-8")
            (folder / ".oculto").write_text("secreto", encoding="utf-8")
            first = plugins.fingerprint(folder)
            self.assertEqual(first, plugins.fingerprint(folder), "reproducible")
            (folder / ".oculto").write_text("cambiado", encoding="utf-8")
            self.assertEqual(first, plugins.fingerprint(folder), "los ocultos no cuentan en la huella")
            (folder / "a.js").write_text("dos", encoding="utf-8")
            self.assertNotEqual(first, plugins.fingerprint(folder), "una letra basta para cambiar la huella")

    @unittest.skipIf(os.name == "nt", "crear enlaces en Windows pide un privilegio que el CI no tiene")
    def test_a_link_inside_the_folder_is_an_error(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.js").write_text("uno", encoding="utf-8")
            (folder / "enlace.js").symlink_to(folder / "a.js")
            with self.assertRaises(ValueError):
                plugins.files_of(folder)

    def test_a_package_that_marks_a_file_as_a_link_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp))
            buffer = bytearray(plugins.pack_folder(folder, folder.name))
            at = buffer.find(b"PK\x01\x02")  # the central header: its external attrs carry the file type
            buffer[at + 38:at + 42] = (0o120000 << 16).to_bytes(4, "little")  # a symlink
            with self.assertRaises(ValueError):
                plugins.read_package(bytes(buffer))

    def test_inside_of_refuses_escapes(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.js").write_text("uno", encoding="utf-8")
            self.assertIsNotNone(plugins.inside_of(folder, "a.js"))
            self.assertIsNone(plugins.inside_of(folder, "../a.js"))
            self.assertIsNone(plugins.inside_of(folder, "/etc/passwd"))
            self.assertIsNone(plugins.inside_of(folder, "no-existe.js"))
            self.assertIsNone(plugins.inside_of(folder, ""))


class ReadPluginTests(unittest.TestCase):
    def test_a_ui_plugin_is_read_with_its_permissions_and_settings(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), settings={"tema": {"type": "choice", "label": "Tema", "options": ["claro", "oscuro"], "default": "claro"}})
            p = plugins.read_plugin(folder)
            self.assertEqual(p["kind"], "ui")
            self.assertEqual(p["problems"], [])
            self.assertEqual(p["entry"], "index.html")
            self.assertIn("storage", p["permissions"])
            self.assertIn("tema", p["settings"])
            self.assertIn("player.read", plugins.signature(p))

    def test_a_ui_plugin_with_problems_is_listed_but_never_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), "roto", permissions=["permiso-que-no-existe"])
            p = plugins.read_plugin(folder)
            self.assertTrue(any("permiso desconocido" in x for x in p["problems"]))
            self.assertFalse(plugins.is_approved(p, {p["id"]: plugins.signature(p)}), "con problemas nunca está aprobado")

    def test_a_full_plugin_gets_a_fingerprint_and_refuses_main_in_the_browser(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_full_plugin(Path(tmp))
            p = plugins.read_plugin(folder)
            self.assertEqual(p["kind"], "full")
            self.assertEqual(p["problems"], [])
            self.assertEqual(len(p["hash"]), 64)
            self.assertIn(p["hash"], plugins.full_signature(p))
            main = write_full_plugin(Path(tmp), "full-main", full={"main": "main.js"})
            (main / "main.js").write_text("x", encoding="utf-8")
            q = plugins.read_plugin(main)
            self.assertTrue(any("main" in x and "navegador" in x for x in q["problems"]), q["problems"])

    def test_a_dev_plugin_is_refused_outside_the_dev_folder(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "de-desarrollo"
            folder.mkdir()
            (folder / "manifest.json").write_text(json.dumps({"name": "dev", "tdt": {"api": 1, "dev": {"page": "page.js"}}}), encoding="utf-8")
            (folder / "page.js").write_text("x", encoding="utf-8")
            p = plugins.read_plugin(folder)
            self.assertTrue(any("desarrollo" in x for x in p["problems"]))

    def test_a_script_plugin_keeps_its_rules(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "de-script"
            folder.mkdir()
            (folder / "inyecta.js").write_text("console.log(1)", encoding="utf-8")
            manifest = {"name": "script", "content_scripts": [{"matches": ["https://example.com/*"], "js": ["inyecta.js"]}]}
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            p = plugins.read_plugin(folder)
            self.assertEqual(p["kind"], "script")
            self.assertEqual(p["problems"], [])
            self.assertEqual(p["sites"], ["https://example.com/*"])
            self.assertEqual(len(p["rules"]), 1)


class PackageTests(unittest.TestCase):
    def test_pack_and_read_back(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp))
            buffer = plugins.pack_folder(folder, folder.name)
            self.assertEqual(buffer, plugins.pack_folder(folder, folder.name), "el paquete es reproducible")
            parsed = plugins.read_package(buffer)
            self.assertEqual(parsed["id"], folder.name)
            self.assertEqual(parsed["files"]["app.js"], b"console.log('hola');\n")

    def test_a_tampered_package_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp))
            buffer = bytearray(plugins.pack_folder(folder, folder.name))
            buffer[len(buffer) // 2] ^= 0xFF
            with self.assertRaises(ValueError):
                plugins.read_package(bytes(buffer))

    def test_a_package_with_an_escape_in_a_name_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp))
            buffer = plugins.pack_folder(folder, folder.name)
            evil = buffer.replace(b"index.html", b"../evilxxx")  # same length: the headers stay consistent
            self.assertNotEqual(evil, buffer)
            with self.assertRaises(ValueError):
                plugins.read_package(evil)

    def test_garbage_is_not_a_package(self) -> None:
        with self.assertRaises(ValueError):
            plugins.read_package(b"esto no es un zip")
        with self.assertRaises(ValueError):
            plugins.read_package(b"PK\x05\x06" + b"\x00" * 18)

    def test_check_link_only_allows_public_https(self) -> None:
        self.assertTrue(plugins.check_link("https://plugins.example.com/x.tdtplugin")["ok"])
        for bad in ("http://plugins.example.com/x", "https://127.0.0.1/x", "https://localhost/x",
                    "https://user:pass@example.com/x", "https://example.com:8443/x", "ftp://example.com/x"):
            self.assertFalse(plugins.check_link(bad)["ok"], bad)


class FakeResponse:
    def __init__(self, status: int = 200, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._at = 0

    def read(self, n: int = -1) -> bytes:
        if self._at >= len(self._body):
            return b""
        chunk = self._body[self._at:] if n < 0 else self._body[self._at:self._at + n]
        self._at += len(chunk)
        return chunk

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


class FakeNet:
    """A ``Net`` of play: ``routes[url]`` is a :class:`FakeResponse` or an exception to raise."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def open(self, url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        self.calls.append(url)
        route = self.routes.get(url)
        if isinstance(route, Exception):
            raise route
        if route is None:
            raise OSError("sin ruta")
        return route


def redirect(url: str, to: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 302, "Found", {"Location": to}, None)


class PluginFetchTests(unittest.TestCase):
    def test_an_allowed_host_answers(self) -> None:
        net = FakeNet({"https://api.example.com/datos": FakeResponse(200, {"Content-Type": "text/plain"}, b"hola")})
        out = plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/datos", {}, net)
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], "hola")
        self.assertEqual(net.calls, ["https://api.example.com/datos"])

    def test_a_host_without_permission_never_leaves(self) -> None:
        net = FakeNet({})
        with self.assertRaises(PluginError) as ctx:
            plugins.plugin_fetch(["network:api.example.com"], "https://otro.example.net/x", {}, net)
        self.assertEqual(ctx.exception.code, "E_PERMISSION")
        self.assertEqual(net.calls, [], "ni se abre la conexión")

    def test_every_redirect_is_checked_again(self) -> None:
        net = FakeNet({
            "https://api.example.com/a": redirect("https://api.example.com/a", "https://api.example.com/b"),
            "https://api.example.com/b": FakeResponse(200, {}, b"bien"),
        })
        out = plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/a", {}, net)
        self.assertEqual(out["text"], "bien")
        net2 = FakeNet({
            "https://api.example.com/a": redirect("https://api.example.com/a", "https://malo.example.net/b"),
        })
        with self.assertRaises(PluginError) as ctx:
            plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/a", {}, net2)
        self.assertEqual(ctx.exception.code, "E_PERMISSION")

    def test_only_public_https_names(self) -> None:
        for url in ("http://api.example.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://user:pw@api.example.com/x"):
            with self.assertRaises(PluginError, msg=url):
                plugins.plugin_fetch(["network:api.example.com"], url, {}, FakeNet({}))

    def test_body_only_on_post_and_bounded(self) -> None:
        net = FakeNet({"https://api.example.com/x": FakeResponse(200, {}, b"ok")})
        with self.assertRaises(PluginError) as ctx:
            plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/x", {"method": "GET", "body": "x"}, net)
        self.assertEqual(ctx.exception.code, "E_INVALID")
        with self.assertRaises(PluginError):
            plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/x", {"method": "POST", "body": "x" * 100_001}, net)
        out = plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/x", {"method": "POST", "body": "datos"}, net)
        self.assertTrue(out["ok"])
        with self.assertRaises(PluginError):
            plugins.plugin_fetch(["network:api.example.com"], "https://api.example.com/x", {"method": "DELETE"}, net)


class PluginStoreTests(AppCase):
    """The store against a temporary data folder: create, approve, enable, permissions, storage, config, serving, full."""

    def setUp(self) -> None:
        super().setUp()
        self.store: PluginStore = self.app.plugins
        self.user_dir = self.store.user_dir

    def test_create_approve_enable_and_the_list_the_host_mounts(self) -> None:
        made = self.store.create("Mi Plugin de Prueba", "ui")
        self.assertTrue(made["ok"], made)
        pid = made["id"]
        p = self.store.find(pid)
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "ui")
        self.assertEqual(p["problems"], [])
        on = self.store.enable(pid, True)
        self.assertFalse(on["ok"])
        self.assertTrue(on.get("needsApproval"), "activar un plugin con permisos siempre pregunta primero")
        self.assertFalse(self.store.approve(pid, "una firma cualquiera")["ok"])
        self.assertTrue(self.store.approve(pid, plugins.signature(p))["ok"])
        self.assertTrue(self.store.enable(pid, True)["ok"])
        listed = self.store.ui_plugins()
        mine = next(q for q in listed if q["id"] == pid)
        self.assertEqual(mine["url"], f"/plugin/{pid}/")
        self.assertIn("player.read", mine["permissions"])
        snap = self.store.snapshot()
        item = next(q for q in snap["plugins"] if q["id"] == pid)
        self.assertTrue(item["enabled"])
        self.assertEqual(snap["mode"], "browser")
        self.assertFalse(self.store.enable(pid, False)["ok"] is False)
        self.assertEqual(self.store.ui_plugins(), [], "apagado ya no se sirve")

    def test_files_are_served_only_to_the_running_plugin(self) -> None:
        folder = write_ui_plugin(self.user_dir)
        self.store.scan()
        pid = folder.name
        self.assertEqual(self.store.serve(pid, "/")[0], 404, "apagado no se sirve nada")
        p = self.store.find(pid)
        self.store.approve(pid, plugins.signature(p))
        self.store.enable(pid, True)
        status, ctype, body = self.store.serve(pid, "/")
        self.assertEqual((status, ctype), (200, "text/html; charset=utf-8"))
        self.assertIn(b"app.js", body)
        self.assertEqual(self.store.serve(pid, "/app.js")[0], 200)
        self.assertEqual(self.store.serve(pid, "/@tdt/sdk.js")[0], 200, "el SDK se sirve de la propia aplicación")
        self.assertEqual(self.store.serve(pid, "/no-existe.js")[0], 404)
        self.assertEqual(self.store.serve(pid, "/../manifest.json")[0], 404)
        self.assertEqual(self.store.serve(pid, "/..%2F..%2Fserver.py")[0], 404)
        (folder / ".secreto").write_text("x", encoding="utf-8")
        self.assertEqual(self.store.serve(pid, "/.secreto")[0], 404)
        (folder / "datos.bin").write_bytes(b"\x00")
        self.assertEqual(self.store.serve(pid, "/datos.bin")[0], 415)
        self.assertEqual(self.store.serve("otro", "/")[0], 404)

    def test_permissions_are_checked_on_every_call(self) -> None:
        write_ui_plugin(self.user_dir)
        self.store.scan()
        pid = "de-prueba"
        p = self.store.find(pid)
        self.store.approve(pid, plugins.signature(p))
        self.store.enable(pid, True)
        out = self.store.call(pid, "storage.set", ["clave", "valor"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.store.call(pid, "storage.get", ["clave"])["result"], "valor")
        self.assertEqual(self.store.call(pid, "storage.keys", [])["result"], ["clave"])
        self.assertTrue(self.store.call(pid, "storage.remove", ["clave"])["result"])
        self.assertIsNone(self.store.call(pid, "storage.get", ["clave"])["result"])
        # quitado el permiso, la misma llamada es denegada
        self.assertTrue(self.store.set_permission(pid, "storage", False)["ok"])
        denied = self.store.call(pid, "storage.set", ["clave", "valor"])
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["code"], "E_PERMISSION")
        self.assertTrue(self.store.set_permission(pid, "storage", True)["ok"])
        # y la red solo a lo que cubre network:
        blocked = self.store.call(pid, "net.fetch", ["https://otro.example.net/"])
        self.assertEqual(blocked["code"], "E_PERMISSION")
        unknown = self.store.call(pid, "hacer.algo.raro", [])
        self.assertEqual(unknown["code"], "E_UNSUPPORTED")
        off = self.store.call("apagado", "storage.keys", [])
        self.assertEqual(off["code"], "E_UNAVAILABLE")

    def test_storage_limits(self) -> None:
        write_ui_plugin(self.user_dir)
        self.store.scan()
        pid = "de-prueba"
        p = self.store.find(pid)
        self.store.approve(pid, plugins.signature(p))
        self.store.enable(pid, True)
        big = self.store.call(pid, "storage.set", ["grande", "x" * 70_000])
        self.assertFalse(big["ok"], "más de 64 KB en un valor no entra")
        bad_key = self.store.call(pid, "storage.set", ["clave con espacios", 1])
        self.assertFalse(bad_key["ok"])

    def test_config_follows_the_declared_schema(self) -> None:
        settings = {"volumen": {"type": "number", "label": "Volumen", "min": 0, "max": 1, "default": 0.5}}
        write_ui_plugin(self.user_dir, settings=settings)
        self.store.scan()
        pid = "de-prueba"
        p = self.store.find(pid)
        self.store.approve(pid, plugins.signature(p))
        self.store.enable(pid, True)
        self.assertEqual(self.store.call(pid, "config.get", ["volumen"])["result"], 0.5)
        self.assertFalse(self.store.set_config(pid, {"volumen": 9})["ok"], "fuera de rango")
        self.assertFalse(self.store.set_config(pid, {"no-existe": 1})["ok"])
        self.assertTrue(self.store.set_config(pid, {"volumen": 0.8})["ok"])
        self.assertEqual(self.store.call(pid, "config.all", [])["result"]["volumen"], 0.8)

    def test_a_full_plugin_runs_only_with_the_approved_fingerprint(self) -> None:
        folder = write_full_plugin(self.user_dir)
        self.store.scan()
        pid = folder.name
        p = self.store.find(pid)
        self.assertEqual(p["kind"], "full")
        self.assertEqual(p["problems"], [])
        self.assertEqual(self.store.full_handover(), [], "sin aprobar no hay entrega")
        self.store.approve(pid, plugins.full_signature(p))
        self.assertTrue(self.store.enable(pid, True)["ok"])
        handed = self.store.full_handover()
        self.assertEqual(len(handed), 1)
        self.assertEqual(handed[0]["id"], pid)
        self.assertIn("ad.started", handed[0]["code"])
        # una letra cambiada en el código y deja de correr hasta que se vuelva a aprobar
        with (folder / "page.js").open("a", encoding="utf-8") as fh:
            fh.write("// cambiada\n")
        self.store.scan()
        self.assertEqual(self.store.full_handover(), [], "la huella cambió: se para y se vuelve a preguntar")
        p2 = self.store.find(pid)
        self.assertTrue(self.store.approve(pid, plugins.full_signature(p2))["ok"])
        self.assertEqual(len(self.store.full_handover()), 1)

    def test_uninstall_removes_the_folder(self) -> None:
        made = self.store.create("Para borrar", "ui")
        pid = made["id"]
        folder = Path(made["folder"])
        self.assertTrue(folder.is_dir())
        self.assertTrue(self.store.uninstall(pid)["ok"])
        self.assertIsNone(self.store.find(pid))
        self.assertFalse(folder.exists())
        bundled = next(p for p in self.store.plugins if p["source"] == "incluido con la aplicación")
        self.assertFalse(self.store.uninstall(bundled["id"])["ok"], "lo incluido con la aplicación no se desinstala")

    def test_pack_inspect_and_commit_install_a_package(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), "empaquetado")
            packed = plugins.pack_folder(folder, folder.name)
        review = self.store.inspect_bytes(packed)
        self.assertNotIn("error", review, review)
        self.assertEqual(review["id"], "empaquetado")
        self.assertEqual(review["kind"], "ui")
        self.assertTrue(review["token"])
        self.assertIn("Instalarlo no lo activa", review["installs"])
        done = self.store.commit(review["token"])
        self.assertTrue(done["ok"], done)
        self.assertIsNotNone(self.store.find("empaquetado"))
        self.assertFalse(self.store.is_on(self.store.find("empaquetado")), "instalar nunca activa")
        again = self.store.commit(review["token"])
        self.assertFalse(again["ok"], "el token ya no vale")

    def test_inspect_refuses_garbage_and_a_changed_file(self) -> None:
        self.assertIn("error", self.store.inspect_bytes(b"no es un paquete"))
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), "cambiado")
            packed = bytearray(plugins.pack_folder(folder, folder.name))
            packed[-30] ^= 0x01
            self.assertIn("error", self.store.inspect_bytes(bytes(packed)))

    def test_inspect_link_checks_the_pinned_sha(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), "de-enlace")
            packed = plugins.pack_folder(folder, folder.name)
        sha = pluginsign.sha256_hex(packed)

        def fetcher(url: str, max_bytes: int) -> dict[str, Any]:
            if url.endswith(".sig"):
                raise ValueError("sin firma")
            return {"url": "https://plugins.example.com/de-enlace.tdtplugin", "buffer": packed}

        review = self.store.inspect_link("https://plugins.example.com/de-enlace.tdtplugin", sha, None, fetcher=fetcher)
        self.assertNotIn("error", review, review)
        self.assertTrue(review["pinned"])
        wrong = self.store.inspect_link("https://plugins.example.com/de-enlace.tdtplugin", "0" * 64, None, fetcher=fetcher)
        self.assertIn("error", wrong)
        nope = self.store.inspect_link("https://127.0.0.1/x.tdtplugin", None, None, fetcher=fetcher)
        self.assertIn("error", nope)

    def test_catalog_marks_what_is_already_installed(self) -> None:
        write_ui_plugin(self.user_dir, "ya-esta")
        self.store.scan()
        have = self.store.find("ya-esta")
        catalog = {"plugins": [{
            "id": "ya-esta", "name": "Ya está", "version": have["version"], "author": "tests",
            "url": "https://plugins.example.com/ya-esta.tdtplugin", "sha256": "a" * 64, "permissions": ["storage"],
        }, {
            "id": "nuevo", "name": "Nuevo", "version": "2.0.0",
            "url": "https://plugins.example.com/nuevo.tdtplugin", "sha256": "b" * 64,
        }, {
            "id": "roto", "name": "Roto", "url": "http://plugins.example.com/x", "sha256": "c" * 64,
        }]}

        def fetcher(url: str, max_bytes: int) -> dict[str, Any]:
            return {"url": url, "buffer": json.dumps(catalog).encode("utf-8")}

        out = self.store.catalog("https://plugins.example.com/catalogo.json", fetcher=fetcher)
        states = {e["id"]: e["state"] for e in out["entries"]}
        self.assertEqual(states, {"ya-esta": "installed", "nuevo": "new"})
        self.assertTrue(any("roto" in p for p in out["problems"]))

    def test_embed_hosts_come_only_from_running_plugins(self) -> None:
        write_ui_plugin(self.user_dir)
        self.store.scan()
        self.assertEqual(self.store.embed_hosts(), [], "apagado no ensancha el frame-src")
        pid = "de-prueba"
        p = self.store.find(pid)
        self.store.approve(pid, plugins.signature(p))
        self.store.enable(pid, True)
        self.assertEqual(self.store.embed_hosts(), ["player.example.tv"])


class PluginRouteTests(ServerCase):
    """The HTTP routes of the plugin server, served by a real TdtServer on an ephemeral port."""

    def post_raw(self, path: str, data: bytes, ctype: str = "application/octet-stream", headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            hdrs = {"Content-Type": ctype, **({"Content-Length": str(len(data))} if data else {}), **(headers or {})}
            conn.request("POST", path, body=data if data else None, headers=hdrs)
            response = conn.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            conn.close()

    def make_running(self, pid: str) -> str:
        """Write a ui plugin with all the test permissions into the user folder and turn it on through the API."""
        write_ui_plugin(self.app.plugins.user_dir, pid)
        status, rescanned = self.post_json("/api/plugins/rescan")
        self.assertEqual(status, 200)
        p = self.app.plugins.find(pid)
        self.assertIsNotNone(p, rescanned)
        status, approved = self.post_json("/api/plugins/approve", {"id": pid, "signature": plugins.signature(p)})
        self.assertTrue(approved["ok"], approved)
        status, enabled = self.post_json("/api/plugins/enable", {"id": pid, "on": True})
        self.assertTrue(enabled["ok"], enabled)
        return pid

    def test_mode_and_list(self) -> None:
        status, mode = self.get_json("/api/mode")
        self.assertEqual((status, mode["mode"]), (200, "browser"))
        status, snap = self.get_json("/api/plugins")
        self.assertEqual(status, 200)
        for key in ("safeMode", "mode", "folder", "plugins"):
            self.assertIn(key, snap)
        self.assertEqual(snap["mode"], "browser")
        self.assertTrue(all("id" in p and "enabled" in p and "kind" in p for p in snap["plugins"]))
        status, made = self.post_json("/api/plugins/create", {"name": "Creado por HTTP", "kind": "ui"})
        self.assertTrue(made["ok"], made)
        self.assertTrue((Path(made["folder"]) / "manifest.json").is_file())
        status, gone = self.post_json("/api/plugins/uninstall", {"id": made["id"]})
        self.assertTrue(gone["ok"], gone)

    def test_the_page_keeps_its_csp_and_gains_the_plugin_embeds(self) -> None:
        status, headers, _ = self.request("GET", "/api/version")
        csp = headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn("frame-src 'self' tdt-plugin:", csp)
        self.assertNotIn("player.example.tv", csp)
        self.make_running("con-embeds")  # its manifest declares embed:player.example.tv
        status, headers, _ = self.request("GET", "/api/version")
        self.assertIn("https://player.example.tv", headers["content-security-policy"])

    def test_plugin_files_are_served_with_their_own_policy(self) -> None:
        pid = self.make_running("servir-archivos")
        status, headers, body = self.request("GET", f"/plugin/{pid}/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        csp = headers["content-security-policy"]
        self.assertTrue(csp.startswith("default-src 'none'"), csp)
        self.assertIn("connect-src 'none'", csp)
        self.assertIn("frame-ancestors 'self' http://127.0.0.1:* http://localhost:*", csp)
        self.assertNotIn("frame-src tdt-plugin:", csp, "la política de la página no se cuela en el plugin")
        self.assertIn(b"/@tdt/sdk.js", body)
        status, headers, body = self.request("GET", f"/plugin/{pid}/app.js")
        self.assertEqual((status, headers["content-type"]), (200, "text/javascript; charset=utf-8"))
        status, _, _ = self.request("GET", f"/plugin/{pid}/no-existe.js")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/plugin/nadie/")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", f"/plugin/{pid}/", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403, "una página de otro sitio no puede pedir archivos de plugin")

    def test_change_routes_answer_and_call_works_over_http(self) -> None:
        pid = self.make_running("por-http")
        status, out = self.post_json("/api/plugins/call", {"id": pid, "method": "storage.set", "args": ["k", "v"]})
        self.assertEqual((status, out["ok"], out["result"]), (200, True, True))
        status, out = self.post_json("/api/plugins/call", {"id": pid, "method": "storage.get", "args": ["k"]})
        self.assertEqual(out["result"], "v")
        status, out = self.post_json("/api/plugins/call", {"id": pid, "method": "net.fetch", "args": ["https://otro.example.net/"]})
        self.assertEqual(out["code"], "E_PERMISSION")
        status, out = self.post_json("/api/plugins/permission", {"id": pid, "name": "storage", "on": False})
        self.assertTrue(out["ok"])
        status, out = self.post_json("/api/plugins/call", {"id": pid, "method": "storage.get", "args": ["k"]})
        self.assertEqual(out["code"], "E_PERMISSION")
        status, out = self.post_json("/api/plugins/enable", {"id": pid, "on": False})
        self.assertTrue(out["ok"])
        status, out = self.get_json("/api/plugins/ui")
        self.assertNotIn(pid, [q["id"] for q in out["plugins"]])

    def test_pack_over_http_is_a_zip_with_its_sha(self) -> None:
        pid = self.make_running("empaquetar")
        status, headers, body = self.request("GET", f"/api/plugins/pack?id={pid}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/zip")
        self.assertEqual(len(headers["x-tdt-sha256"]), 64)
        self.assertEqual(headers["x-tdt-sha256"], pluginsign.sha256_hex(body))
        self.assertTrue(body.startswith(b"PK"))
        self.assertIn(f'filename="{pid}-', headers["content-disposition"])

    def test_install_inspect_takes_a_raw_package_and_commit_installs_it(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), "de-paquete")
            packed = plugins.pack_folder(folder, folder.name)
        status, _, body = self.post_raw("/api/plugins/install/inspect", packed, "application/tdtplugin")
        self.assertEqual(status, 200, body)
        review = json.loads(body)
        self.assertEqual(review["id"], "de-paquete")
        self.assertTrue(review["token"])
        status, done = self.post_json("/api/plugins/install/commit", {"token": review["token"]})
        self.assertEqual(status, 200)
        self.assertTrue(done["ok"], done)
        self.assertIsNotNone(self.app.plugins.find("de-paquete"))
        status, _, body = self.post_raw("/api/plugins/install/inspect", b"basura", "application/tdtplugin")
        self.assertEqual(status, 400)

    def test_install_inspect_by_link_takes_json(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = write_ui_plugin(Path(tmp), "de-enlace-http")
            packed = plugins.pack_folder(folder, folder.name)
        sha = pluginsign.sha256_hex(packed)

        def fake_download(url: str, max_bytes: int, net: Any = None) -> dict[str, Any]:
            if url.endswith(".sig"):
                raise ValueError("sin firma")
            return {"url": "https://plugins.example.com/de-enlace-http.tdtplugin", "buffer": packed}

        import tdtlocal.plugins as pl
        original = pl.download
        pl.download = fake_download  # type: ignore[assignment]
        try:
            status, review = self.post_json("/api/plugins/install/inspect", {
                "url": "https://plugins.example.com/de-enlace-http.tdtplugin", "sha256": sha,
            })
        finally:
            pl.download = original  # type: ignore[assignment]
        self.assertEqual(status, 200, review)
        self.assertEqual(review["id"], "de-enlace-http")
        self.assertTrue(review["pinned"])

    def test_full_handover_route(self) -> None:
        folder = write_full_plugin(self.app.plugins.user_dir)
        self.app.plugins.scan()
        pid = folder.name
        p = self.app.plugins.find(pid)
        self.app.plugins.approve(pid, plugins.full_signature(p))
        self.app.plugins.enable(pid, True)
        status, out = self.get_json("/api/plugins/full")
        self.assertEqual(status, 200)
        mine = next(q for q in out["plugins"] if q["id"] == pid)
        self.assertIn("ad.started", mine["code"])
        self.assertEqual(len(mine["hash"]), 64)


if __name__ == "__main__":
    unittest.main()
