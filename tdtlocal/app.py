"""Wiring of all services and their background threads."""

from __future__ import annotations

import threading
from typing import Any

from . import __version__, ads, u7d
from .ai import Assistant
from .config import Config
from .control import ControlBus
from .epg import EpgSyncer
from .feeds import FeedSyncer
from .health import HealthChecker
from .hooks import Hooks
from .logos import LogoCache
from .logbook import Logbook
from . import safemode
from .devplugins import DevStore
from .net import Net
from .prefs import PrefsStore
from .plugins import PluginStore
from .rules import RulesStore
from .scte_watch import ScteWatcher
from .settings import BY_NAME, SettingsStore
from .store import Store, channel_ok, source_ok
from .stt import Transcriber
from .trusted import Loader
from .u7d import U7d
from .whisper_models import ModelDownloader


class App:
    """Owns the services. The HTTP handler reaches everything through ``server.app``."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.settings = SettingsStore(config)
        self.settings.load()  # before the services: they read the configuration when they are created
        self.prefs = PrefsStore(config.data_dir / "prefs.json")  # the page's preferences, kept on the server (ROADMAP: not per-origin)
        self.stop = threading.Event()
        self.hooks = Hooks()  # what the application lets others look at, change or replace (docs/HACKEABLE.md)
        self.trusted = Loader(self.hooks)  # the plugins with all the power that run in the server
        self.net = Net(allow_private=config.allow_private)
        self.plugins = PluginStore(config, self.net)  # the plugins of the browser mode (docs/PARIDAD-NAVEGADOR.md)
        self.dev = DevStore(config)  # the development plugins of the browser mode (same doc, phase 4)
        self.logbook = Logbook(config.data_dir / "browser" / "log.txt")  # the log of the browser mode (same doc, phase 6)
        # safe mode (safemode.py, the desktop app's safe-mode.js): --safe, TDT_SAFE_MODE=1, the settings' button, or two bad exits in a row
        self.crash_guard = safemode.CrashGuard(config.data_dir / "browser" / "run.json")
        self.safe_mode = safemode.decide(
            flag=safemode.argv_wants_safe(), unclean=self.crash_guard.peek(), forced=safemode.read_forced(config.data_dir)
        )
        self.plugins.set_safe(self.safe_mode["text"] if self.safe_mode["safe"] else "")
        self.dev.set_safe(self.safe_mode["text"] if self.safe_mode["safe"] else "")
        self.store = Store(config, self.net)
        self.feeds = FeedSyncer(config, self.store, self.net)
        self.epg = EpgSyncer(config, self.store, self.net)
        self.health = HealthChecker(self.store, self.net, stop=self.stop)
        self.u7d = U7d(self.net, self._allow_cover)
        self.scte = ScteWatcher(config, self.store, self.net, hooks=self.hooks)
        self.control = ControlBus(config)
        self.rules = RulesStore(config.data_dir / "rules.json")
        self.rules.load()
        self.assistant = Assistant(config, self.store)
        self.transcriber = Transcriber(config)
        self.models = ModelDownloader(config, self.net)
        self.logos = LogoCache(self.store, self.net, config.data_dir / "logos")

    def start_background(self) -> None:
        """Load the cache and start the refresh threads."""
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self.crash_guard.start()  # this run is marked as «running»: a start that finds it without a clean exit counts as a bad one
        self.logbook.add("servidor", "INFO",
                         f"TDT local {__version__} · modo navegador · datos en {self.config.data_dir}"
                         + ("" if self.config.loopback_only else " · AVISO: accesible desde la red local, sin autenticación")
                         + (f" · modo seguro: {self.safe_mode['text']}" if self.safe_mode["safe"] else ""))
        on = [p["id"] for p in self.plugins.plugins if self.plugins.is_on(p)]
        self.logbook.add("plugins", "INFO", f"{len(self.plugins.plugins)} encontrados; activados: " + (", ".join(on) or "ninguno"))
        for p in self.plugins.plugins:
            if p["problems"]:
                self.logbook.add("plugins", "WARN", p["id"] + " no se puede usar: " + "; ".join(p["problems"]))
        self.store.load()
        self.scte.load()
        self.control.announce()
        threading.Thread(target=self.feeds.run, args=(self.stop, self._after_sync), daemon=True, name="feeds").start()
        threading.Thread(target=self.epg.run, args=(self.stop,), daemon=True, name="epg").start()
        threading.Thread(target=self.scte.run, args=(self.stop,), daemon=True, name="scte").start()

    def _after_sync(self) -> None:
        if self.config.auto_check:  # read now, not at start-up: it can be changed in the settings
            self.health.start()

    def apply_settings(self, changed: list[str]) -> None:
        """Tell the services whose behaviour depends on the settings that were just changed."""
        affected = {service for name in changed for service in BY_NAME[name].affects}
        if "stt" in affected:
            self.transcriber.reconfigure()
        if "epg" in affected:
            self.epg.refresh_soon()
        if "feeds" in affected:
            self.feeds.refresh_soon()
        if "scte" in affected:
            self.scte.reconfigure()

    def set_safe_mode(self, safe: bool) -> dict[str, Any]:
        """Turn the safe mode on or off at runtime (the settings' button); it is also written for the next starts."""
        safemode.write_forced(self.config.data_dir, safe)
        text = safemode.WHY["forced"] if safe else ""
        if not safe:
            self.crash_guard.clean_exit()  # leaving the safe mode counts as a proper end: the count starts again
        self.safe_mode = {"safe": bool(safe), "why": "forced" if safe else "", "text": text}
        self.plugins.set_safe(text)
        self.dev.set_safe(text)
        self.plugins.scan()
        self.dev.scan()
        self.logbook.add("servidor", "WARN" if safe else "INFO",
                         "modo seguro " + ("activado" if safe else "desactivado") + (f": {text}" if text else ""))
        return {"ok": True, "safe": bool(safe), "text": text, "list": self.plugins.snapshot()}

    def close(self) -> None:
        self.stop.set()
        self.logbook.add("servidor", "INFO", "cierre limpio")
        self.control.close()
        self.transcriber.close()
        self.plugins.close()
        self.crash_guard.clean_exit()  # a proper close is not a bad exit for the next start

    def allow_covers(self, urls: Any) -> int:
        """Let the logo proxy fetch the covers a page names (see ``u7d.cover_ok``). Returns how many it took."""
        taken = 0
        for url in (urls if isinstance(urls, list) else [])[: u7d.MAX_COVERS]:
            if u7d.cover_ok(url):
                self._allow_cover(url)
                taken += 1
        return taken

    def _allow_cover(self, url: str) -> None:
        with self.store.lock:
            self.store.cover_urls.add(url)

    def channels_payload(self) -> dict[str, Any]:
        """The JSON behind ``/api/channels``: every channel with its health, plus list metadata."""
        store = self.store
        with store.lock:
            channels, status = store.all_channels(), store.status
            meta, health = dict(store.meta), dict(store.health)
            radio = {k: store.radio_meta.get(k) for k in ("checked", "updated", "error")}
            out = []
            for ch in channels:
                fmts = ch.get("fmts") or []
                sources = [
                    {"u": u, "ok": source_ok(ch, u, status), "f": fmts[i] if i < len(fmts) else ""}
                    for i, u in enumerate(ch["sources"])
                ]
                item = {k: v for k, v in ch.items() if k not in ("fmts", "headers")}
                item["sources"], item["ok"] = sources, channel_ok(ch, status)
                item["kind"] = item.get("kind") or "tv"
                item["hdr"] = ch["id"] in store.custom_headers  # the page then tells the proxy whose headers to use
                out.append(item)
        meta.pop("etag", None)
        meta["radio"] = radio
        meta["ai"] = self.config.ai_enabled
        meta["version"] = __version__
        meta["quit"] = self.config.allow_quit
        meta["stt"] = self.transcriber.available()
        meta["scte"] = self.config.scte_enabled
        meta["ads"] = ads.payload(self.config)
        return {"meta": meta, "health": health, "channels": out}
