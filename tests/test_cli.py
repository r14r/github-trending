"""Browser-free tests for repository extraction, fallback, and CSV safety."""

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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def load_scraper():
    script = Path(__file__).resolve().parents[1] / "github_trending.py"
    spec = importlib.util.spec_from_file_location("github_trending_tested", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO_NAMES = [
    "ripienaar/free-for-dev", "electron/electron",
    "paperless-ngx/paperless-ngx", "hyprwm/Hyprland", "k3s-io/k3s",
    "apache/arrow", "jrouwe/JoltPhysics", "nitrojs/nitro",
    "recastnavigation/recastnavigation", "codecrafters-io/build-your-own-x",
    "openxla/xla", "jesseduffield/lazygit", "microsoft/onnxruntime",
    "f/prompts.chat", "apache/dolphinscheduler", "NVIDIA/Megatron-LM",
    "poteto/hiring-without-whiteboards", "tailscale/tailscale",
    "dortania/OpenCore-Legacy-Patcher", "chidiwilliams/buzz",
    "IceWhaleTech/CasaOS", "Developer-Y/cs-video-courses",
    "tiann/KernelSU", "microsoft/playwright",
]


def sample_html(names=REPO_NAMES):
    return (
        "<html><body>"
        + "".join(
            '<article class="Box-row"><h2><a href="/' + name + '">'
            + name + '</a></h2><p class="col-9">Description of '
            + name + "</p></article>"
            for name in names
        )
        + "</body></html>"
    )


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_scraper()

    def test_cli_flags(self):
        with patch.object(sys, "argv", ["github_trending.py"]):
            args = self.app.parse_args()
            self.assertFalse(args.details)
            self.assertFalse(args.browser)
        with patch.object(
            sys, "argv", ["github_trending.py", "--details", "--browser"]
        ):
            args = self.app.parse_args()
            self.assertTrue(args.details)
            self.assertTrue(args.browser)

    def test_aligned_progress_and_failed_status(self):
        label = "[1/3] Fetching trending page..."
        width = self.app.STATUS_COLUMN_WIDTH
        output = io.StringIO()
        with redirect_stdout(output):
            with self.app.ProgressStep(label):
                pass
        self.assertEqual(output.getvalue(), f"{label:<{width}}OK\n")

        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, "failed"):
                with self.app.ProgressStep(label):
                    raise ValueError("failed")
        self.assertEqual(output.getvalue(), f"{label:<{width}}FAILED\n")

    def test_info_logs_only_with_details(self):
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level
        try:
            for details in (False, True):
                output = io.StringIO()
                args = argparse.Namespace(silent=False, log=None, details=details)
                with patch.object(sys, "stderr", output):
                    self.app.configure_output(args)
                    logging.getLogger("nodriver.core.browser").info("diagnostic")
                    logging.getLogger("nodriver.core.browser").error("important")
                value = output.getvalue()
                self.assertEqual("diagnostic" in value, details)
                self.assertIn("important", value)
        finally:
            for handler in root.handlers[:]:
                root.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                root.addHandler(handler)
            root.setLevel(original_level)


class ParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_scraper()

    def test_extracts_all_24_card_links_in_page_order(self):
        repositories = self.app.parse_repositories(sample_html())
        self.assertEqual(len(repositories), 24)
        self.assertEqual([row["title"] for row in repositories], REPO_NAMES)
        self.assertEqual(
            repositories[0],
            {
                "title": "ripienaar/free-for-dev",
                "desc": "Description of ripienaar/free-for-dev",
                "link": "https://github.com/ripienaar/free-for-dev",
            },
        )
        self.assertEqual(
            repositories[-1]["link"], "https://github.com/microsoft/playwright"
        )

    def test_rejects_missing_and_duplicate_cards(self):
        with self.assertRaisesRegex(RuntimeError, "No GitHub Trending"):
            self.app.parse_repositories("<html></html>")
        with self.assertRaisesRegex(RuntimeError, "without a repository link"):
            self.app.parse_repositories('<article class="Box-row"></article>')
        with self.assertRaisesRegex(RuntimeError, "Duplicate repository"):
            self.app.parse_repositories(sample_html(["a/b", "a/b"]))

    def test_empty_description_is_allowed(self):
        html = (
            '<article class="Box-row"><h2><a href="/owner/repo">'
            "repo</a></h2></article>"
        )
        self.assertEqual(self.app.parse_repositories(html)[0]["desc"], "")


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_scraper()

    def test_rerun_replaces_only_matching_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trending.csv"
            repo = {"title": "owner/repo", "desc": "description",
                    "link": "https://github.com/owner/repo"}
            self.assertEqual(
                self.app.update_csv(path, [repo], "2026-11-01"), (0, 1)
            )
            self.assertEqual(
                self.app.update_csv(path, [repo], "2026-11-02"), (0, 2)
            )
            self.assertEqual(
                self.app.update_csv(path, [repo], "2026-11-01"), (1, 2)
            )
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


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_retry_after_transient_failure(self):
        app = load_scraper()
        with (
            patch.object(
                app, "download_html",
                side_effect=[OSError("timeout"), sample_html(["a/b"])],
            ) as download,
            patch.object(app.asyncio, "sleep", AsyncMock()) as sleep,
        ):
            result = await app.fetch_html_http(attempts=2)
        self.assertIn("a/b", result)
        self.assertEqual(download.call_count, 2)
        sleep.assert_awaited_once()

    async def test_browser_retries_devtools_connection_failure(self):
        app = load_scraper()
        page = SimpleNamespace(
            select=AsyncMock(return_value=object()),
            get_content=AsyncMock(return_value=sample_html(["owner/repo"])),
        )
        browser = SimpleNamespace(
            get=AsyncMock(return_value=page),
            stop=MagicMock(),
        )
        nodriver = SimpleNamespace(
            start=AsyncMock(
                side_effect=[RuntimeError("Failed to connect to browser"), browser]
            ),
        )
        with (
            patch.dict(sys.modules, {"nodriver": nodriver}),
            patch.object(app.asyncio, "sleep", AsyncMock()),
        ):
            result = await app.fetch_html_browser(attempts=2)
        self.assertIn("owner/repo", result)
        self.assertEqual(nodriver.start.await_count, 2)
        browser.stop.assert_called_once()


class ScraperTests(unittest.IsolatedAsyncioTestCase):
    async def test_https_success_shows_source_and_every_repository(self):
        app = load_scraper()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            output = io.StringIO()
            browser = AsyncMock(side_effect=AssertionError("browser not needed"))
            with (
                patch.object(app, "fetch_html_http", AsyncMock(return_value=sample_html())),
                patch.object(app, "fetch_html_browser", browser),
                redirect_stdout(output),
            ):
                await app.scrape(path)
            result = output.getvalue()
            self.assertIn("Source: https://github.com/trending", result)
            self.assertIn("OK (HTTPS)", result)
            self.assertIn("OK (24 repositories found, HTTPS)", result)
            self.assertIn("24 saved | 0 replaced | 24 total records", result)
            for repo in REPO_NAMES:
                self.assertIn("https://github.com/" + repo, result)
            browser.assert_not_awaited()
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 24)

    async def test_https_connection_error_uses_browser_fallback(self):
        app = load_scraper()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            output = io.StringIO()
            with (
                patch.object(
                    app, "fetch_html_http",
                    AsyncMock(side_effect=ConnectionError("HTTP unavailable")),
                ),
                patch.object(
                    app, "fetch_html_browser",
                    AsyncMock(return_value=sample_html(["owner/repo"])),
                ) as browser,
                redirect_stdout(output),
            ):
                await app.scrape(path)
            self.assertIn("OK (Chrome fallback)", output.getvalue())
            self.assertIn("https://github.com/owner/repo", output.getvalue())
            browser.assert_awaited_once()

    async def test_unparseable_https_html_uses_browser_fallback(self):
        app = load_scraper()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            output = io.StringIO()
            with (
                patch.object(app, "fetch_html_http", AsyncMock(return_value="<html/>")),
                patch.object(
                    app, "fetch_html_browser",
                    AsyncMock(return_value=sample_html(["owner/repo"])),
                ) as browser,
                redirect_stdout(output),
            ):
                await app.scrape(path)
            self.assertIn("OK (1 repository found, Chrome fallback)", output.getvalue())
            browser.assert_awaited_once()

    async def test_both_fetchers_fail_without_changing_history(self):
        app = load_scraper()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            path.write_text("existing CSV", encoding="utf-8")
            output = io.StringIO()
            with (
                patch.object(app, "fetch_html_http", AsyncMock(side_effect=OSError("offline"))),
                patch.object(
                    app, "fetch_html_browser",
                    AsyncMock(side_effect=RuntimeError("Failed to connect to browser")),
                ),
                redirect_stdout(output),
            ):
                with self.assertRaisesRegex(RuntimeError, "Chrome fallback failed"):
                    await app.scrape(path)
            self.assertIn("FAILED", output.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), "existing CSV")


if __name__ == "__main__":
    unittest.main()
