"""The example plugins of ``docs/ejemplos`` must pass the server's own validation (the same one an installed plugin
goes through), and their ``entry`` must exist. The ui examples must have no problems in browser mode (a ``main``
scope is the dev example, handled by devplugins, not here)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tdtlocal.plugins import discover

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "docs" / "ejemplos"


class ExamplesTests(unittest.TestCase):
    def test_every_ui_example_validates_clean(self) -> None:
        found = {p["id"]: p for p in discover([{"dir": str(EXAMPLES), "label": "ejemplos"}])}
        ui = []
        for folder in sorted(EXAMPLES.iterdir()):
            manifest = folder / "manifest.json"
            if not manifest.is_file():
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("tdt", {}).get("api") == 1:
                ui.append(folder)
        self.assertGreaterEqual(len(ui), 5)  # saludo, ahora, pantalla, tiempo, teletexto, agenda, apagado…
        api = json.loads((ROOT / "tdtlocal" / "static" / "plugin-api.json").read_text(encoding="utf-8"))
        for folder in ui:
            plugin = found.get(folder.name)
            self.assertIsNotNone(plugin, f"{folder.name} no se ha encontrado")
            self.assertEqual(plugin["problems"], [], f"{folder.name}: {plugin['problems']}")
            self.assertTrue((folder / plugin["entry"]).is_file(), f"{folder.name}: falta {plugin['entry']}")
            for perm in plugin["permissions"]:  # every declared permission must exist in the API spec
                self.assertTrue(perm in api["permissions"] or perm.startswith("network:"),
                                f"{folder.name}: permiso desconocido {perm}")

    def test_new_examples_are_ui_plugins_with_the_permissions_they_use(self) -> None:
        for name, perms in (("agenda", {"channels.read", "epg.read", "player.read", "player.control", "storage", "ui.toast"}),
                            ("apagado", {"player.read", "player.control", "epg.read", "storage", "ui.toast"}),
                            ("cine", {"player.read", "player.control", "epg.read", "storage", "ui.toast"}),
                            ("diario", {"player.read", "storage", "ui.toast"}),
                            ("mosaico", {"player.read", "player.control", "channels.read", "storage"})):
            data = json.loads((EXAMPLES / name / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(data["tdt"]["api"], 1, name)
            self.assertEqual(set(data["tdt"]["permissions"]), perms, name)
            self.assertIn("settings", data["tdt"], name)


if __name__ == "__main__":
    unittest.main()
