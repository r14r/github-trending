"""Regression tests for CLI logging and CSV snapshot behavior.

Third-party packages are stubbed so these tests run without Chrome or network.
"""

import argparse
import asyncio
import csv
import importlib.util
import io
import logging
import sys
import tempfile
import unittest

from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def load_scraper():
    modules = {
        name: ModuleType(name)
        for name in ("nodriver", "parsel", "crawlee", "crawlee.crawlers")
    }
    modules["parsel"].Selector = object
    modules["crawlee"].ConcurrencySettings = object
    modules["crawlee.crawlers"].BasicCrawler = object
    modules["crawlee.crawlers"].BasicCrawlingContext = object

    script = Path(__file__).resolve().parents[1] / "github_trending.py"
    spec = importlib.util.spec_from_file_location("github_trending_tested", script)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_scraper()

    def test_details_flag_defaults_off_and_can_be_enabled(self):
        with patch.object(sys, "argv", ["github_trending.py"]):
            self.assertFalse(self.app.parse_args().details)
        with patch.object(sys, "argv", ["github_trending.py", "--details"]):
            self.assertTrue(self.app.parse_args().details)

    def test_library_info_logs_only_appear_with_details(self):
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        try:
            for details in (False, True):
                output = io.StringIO()
                args = argparse.Namespace(silent=False, log=None, details=details)
                with patch.object(sys, "stderr", output):
                    self.app.configure_output(args)
                    logging.getLogger("nodriver.core.browser").info("browser diagnostic")
                    logging.getLogger("crawlee.crawlers").info("crawler diagnostic")
                    logging.getLogger("crawlee.crawlers").error("important error")
                value = output.getvalue()
                self.assertEqual("browser diagnostic" in value, details)
                self.assertEqual("crawler diagnostic" in value, details)
                self.assertIn("important error", value)
        finally:
            for handler in root.handlers[:]:
                root.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                root.addHandler(handler)
            root.setLevel(original_level)


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_scraper()

    def test_rerun_replaces_only_matching_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trending.csv"
            repo = {"title": "owner/repo", "desc": "description",
                    "link": "https://github.com/owner/repo"}
            replaced, total = self.app.update_csv(path, [repo], "2026-11-01")
            self.assertEqual((replaced, total), (0, 1))
            replaced, total = self.app.update_csv(path, [repo], "2026-11-02")
            self.assertEqual((replaced, total), (0, 2))
            replaced, total = self.app.update_csv(path, [repo], "2026-11-01")
            self.assertEqual((replaced, total), (1, 2))
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)

    def test_empty_snapshot_does_not_modify_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trending.csv"
            path.write_text("original data", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "zero repositories"):
                self.app.update_csv(path, [], "2026-11-01")
            self.assertEqual(path.read_text(encoding="utf-8"), "original data")


class ScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_reports_progress_and_saves_csv(self):
        app = load_scraper()
        browser = SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(
                select=AsyncMock(return_value=object()),
                get_content=AsyncMock(return_value="<html></html>"),
            )),
            stop=MagicMock(),
        )

        class FakeArticle:
            def css(self, query):
                return SimpleNamespace(get=lambda: "/owner/repo")

            def xpath(self, query):
                return SimpleNamespace(getall=lambda: ["Test", " repository"])

        class FakeSelector:
            def __init__(self, text):
                pass

            def css(self, query):
                return [FakeArticle()]

        class FakeCrawler:
            def __init__(self, **kwargs):
                self.router = SimpleNamespace(default_handler=self.register)

            def register(self, handler):
                self.handler = handler
                return handler

            async def run(self, urls):
                context = SimpleNamespace(
                    request=SimpleNamespace(url=urls[0]),
                    log=SimpleNamespace(info=lambda message: None),
                    push_data=AsyncMock(),
                )
                await self.handler(context)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            output = io.StringIO()
            with (
                patch.object(app.uc, "start", AsyncMock(return_value=browser), create=True),
                patch.object(app, "BasicCrawler", FakeCrawler),
                patch.object(app, "ConcurrencySettings", lambda **kwargs: None),
                patch.object(app, "Selector", FakeSelector),
                redirect_stdout(output),
            ):
                await app.scrape(path)
            result = output.getvalue()
            self.assertIn("[1/3] Starting browser", result)
            self.assertIn("[2/3] Fetching trending repositories", result)
            self.assertIn("[3/3] Updating CSV snapshot", result)
            self.assertIn("Result: 1 saved", result)
            browser.stop.assert_called_once()
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["title"], "owner/repo")


if __name__ == "__main__":
    unittest.main()
