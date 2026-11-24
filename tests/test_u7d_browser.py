"""The lector that the bookmarklet carries, served by the server itself (GET /u7d-lector.js) — phase 7 of
docs/PARIDAD-NAVEGADOR.md: in a browser the Atresmedia U7D list is read by that script inside the channel's public
page (the desktop app reads it with its plugin in its own window)."""

from __future__ import annotations

import unittest

from .helpers import ServerCase


class LectorRouteTests(ServerCase):
    def test_the_lector_is_served_as_javascript(self) -> None:
        status, headers, body = self.request("GET", "/u7d-lector.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("content-type", ""))
        text = body.decode("utf-8")
        self.assertIn("tdt-u7d", text)  # the message tag the page accepts
        self.assertIn("__TDT_U7D_TO__", text)  # the bookmarklet sets the origin of TDT local before the code
        self.assertIn("module.exports", text)  # node loads it to test it (tests/js/atres-browser.test.mjs)

    def test_it_is_not_reachable_as_a_page(self) -> None:
        # it is a plain script for a bookmarklet/console, not a document: the page CSP of index.html still applies to HTML
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))


if __name__ == "__main__":
    unittest.main()
