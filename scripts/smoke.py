#!/usr/bin/env python3
"""Smoke test of the browser mode: start the real server on an ephemeral port and ask for the pages and routes the
page needs at load, then stop. It catches, right after an upstream sync, that the page still serves before merging.

    python scripts/smoke.py

Exits non-zero if anything fails. Standard library only.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as «python scripts/smoke.py» from anywhere

from tdtlocal.app import App
from tdtlocal.config import Config
from tdtlocal.server import create_server

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the system proxy must not see loopback

# path -> a fragment the answer must contain
WANTED = {
    "/": "<html",
    "/api/mode": '"browser"',
    "/manifest.webmanifest": '"standalone"',
    "/sw.js": "addEventListener('fetch'",
    "/api/logs": '"lines"',
    "/u7d-lector.js": "tdt-u7d",
    "/api/settings": "{",
    "/api/channels": "channels",
}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(data_dir=Path(tmp) / "data", models_dir=Path(tmp) / "models", port=0,
                        allow_private=True, ai_enabled=False, stt_enabled=False)
        app = App(config)
        httpd = create_server(app)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        port = httpd.server_address[1]
        base = f"http://127.0.0.1:{port}"
        failed = []
        try:
            for path, needle in WANTED.items():
                try:
                    with NO_PROXY.open(base + path, timeout=10) as r:
                        body = r.read(2_000_000).decode("utf-8", "replace")
                    if r.status != 200 or needle not in body:
                        failed.append(f"{path}: estado {r.status}, falta {needle!r}")
                    else:
                        print(f"OK   {path} ({len(body)} bytes)")
                except Exception as exc:  # noqa: BLE001 - the smoke test reports everything
                    failed.append(f"{path}: {exc}")
            # the page's CSP must not have lost 'self' for scripts
            with NO_PROXY.open(base + "/", timeout=10) as r:
                csp = r.headers.get("Content-Security-Policy", "")
            if "script-src 'self'" not in csp or "unsafe-inline" in csp:
                failed.append(f"/ CSP mala: {csp!r}")
            else:
                print("OK   CSP de la página (script-src 'self', sin unsafe-inline)")
        finally:
            httpd.shutdown()
            httpd.server_close()
            app.close()
        if failed:
            print("\nFALLA el smoke test:")
            for line in failed:
                print("  " + line)
            return 1
        print(f"\nSmoke test en verde: {len(WANTED) + 1} comprobaciones contra {base}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
