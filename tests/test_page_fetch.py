from __future__ import annotations

import unittest
from unittest.mock import patch

from safeplate.page_fetch import HtmlPage, PageFetchError, _looks_js_empty, fetch_html_page


class FetchModeRoutingTests(unittest.TestCase):
    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(PageFetchError):
            fetch_html_page("https://x.com", user_agent="ua", fetch_mode="weird")

    def test_dynamic_mode_renders(self) -> None:
        with patch("safeplate.page_fetch.can_fetch_url") as robots, patch(
            "safeplate.dynamic_fetch.render_html", return_value="<html>rendered</html>"
        ):
            robots.return_value.allowed = True
            page = fetch_html_page("https://x.com", user_agent="ua", fetch_mode="dynamic")
        self.assertEqual(page.fetch_method, "dynamic_html")
        self.assertIn("rendered", page.html)

    def test_auto_falls_back_to_render_when_static_looks_empty(self) -> None:
        empty_spa = '<html><body><div id="root"></div></body></html>'
        with patch(
            "safeplate.page_fetch._fetch_static_html",
            return_value=HtmlPage("u", "u", empty_spa, "static_html"),
        ), patch("safeplate.page_fetch.can_fetch_url") as robots, patch(
            "safeplate.dynamic_fetch.render_html", return_value="<html>full menu $12</html>"
        ):
            robots.return_value.allowed = True
            page = fetch_html_page("https://x.com", user_agent="ua", fetch_mode="auto")
        self.assertEqual(page.fetch_method, "dynamic_html")

    def test_looks_js_empty_heuristic(self) -> None:
        self.assertTrue(_looks_js_empty('<html><body><div id="root"></div></body></html>'))
        self.assertFalse(_looks_js_empty("<html><body>" + "Pad Thai $12 " * 5000 + "</body></html>"))
        self.assertFalse(_looks_js_empty("<html><body><p>Static menu</p></body></html>"))


if __name__ == "__main__":
    unittest.main()
