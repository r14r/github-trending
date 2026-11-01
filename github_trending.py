#!/usr/bin/env python3

import argparse
import asyncio
import csv
import logging
import os
import sys

from urllib.request import Request, urlopen

from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

from parsel import Selector


# ============================================================================
# Configuration
# ============================================================================

URL = "https://github.com/trending"

DEFAULT_OUTPUT_FILE = Path(
    "github_trending.csv"
)

CSV_FIELDS = [
    "date",
    "title",
    "desc",
    "link",
]

STATUS_COLUMN_WIDTH = 46


class ProgressStep:
    """Print a live progress label and complete it with an aligned status.

    Detailed mode uses separate lines so third-party log messages cannot
    interrupt an unfinished status line.
    """

    def __init__(self, label: str, *, details: bool = False) -> None:
        self.label = label
        self.details = details
        self.status = "OK"

    def __enter__(self):
        if self.details:
            print(self.label, flush=True)
        else:
            print(f"{self.label:<{STATUS_COLUMN_WIDTH}}", end="", flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        status = "FAILED" if exc_type is not None else self.status
        prefix = "      " if self.details else ""
        print(f"{prefix}{status}", flush=True)


# Keep redirected output streams alive until interpreter shutdown.
#
# nodriver performs some cleanup using atexit handlers and can still emit
# logging messages after main() has returned.
_OUTPUT_STREAM = None


# ============================================================================
# Command-line arguments
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape GitHub Trending repositories and maintain "
            "a historical CSV."
        )
    )

    parser.add_argument(
        "--details",
        action="store_true",
        help="Show detailed scraper and browser diagnostic logs.",
    )

    parser.add_argument(
        "--browser",
        action="store_true",
        help="Force Chrome/nodriver instead of the default HTTPS fetch.",
    )

    parser.add_argument(
        "--silent",
        action="store_true",
        help=(
            "Suppress console output. If --log is specified, "
            "output is written to the logfile instead."
        ),
    )

    parser.add_argument(
        "--log",
        type=Path,
        metavar="FILENAME",
        help=(
            "Write stdout, stderr and logging to the specified file."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        metavar="FILENAME",
        help=(
            "CSV output filename "
            f"(default: {DEFAULT_OUTPUT_FILE})."
        ),
    )

    command_group = parser.add_mutually_exclusive_group()

    command_group.add_argument(
        "--stats",
        action="store_true",
        help=(
            "Show statistics for the existing CSV and exit "
            "without scraping GitHub."
        ),
    )

    command_group.add_argument(
        "--snapshots",
        action="store_true",
        help=(
            "List all stored snapshots and exit "
            "without scraping GitHub."
        ),
    )

    return parser.parse_args()


# ============================================================================
# Output / logging
# ============================================================================


def configure_output(
    args: argparse.Namespace,
) -> None:
    """
    Configure process-wide stdout, stderr and logging.

    Normal:
        terminal with concise progress and errors

    --details:
        terminal with library diagnostics

    --silent:
        /dev/null

    --log FILE:
        FILE

    --silent --log FILE:
        FILE

    The redirected stream is deliberately kept open until interpreter
    shutdown because nodriver may log from an atexit cleanup handler.
    """

    global _OUTPUT_STREAM

    # ------------------------------------------------------------------
    # Logfile
    # ------------------------------------------------------------------

    if args.log:
        args.log.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        _OUTPUT_STREAM = args.log.open(
            "a",
            encoding="utf-8",
            buffering=1,
        )

        sys.stdout = _OUTPUT_STREAM
        sys.stderr = _OUTPUT_STREAM

    # ------------------------------------------------------------------
    # Silent
    # ------------------------------------------------------------------

    elif args.silent:
        _OUTPUT_STREAM = open(
            os.devnull,
            "w",
            encoding="utf-8",
        )

        sys.stdout = _OUTPUT_STREAM
        sys.stderr = _OUTPUT_STREAM

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    logging.basicConfig(
        level=logging.INFO if args.details else logging.ERROR,
        format=(
            "%(asctime)s "
            "[%(name)s] "
            "%(levelname)s "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def flush_output() -> None:
    """
    Flush but deliberately do not close stdout/stderr.
    """

    try:
        sys.stdout.flush()
    except Exception:
        pass

    try:
        sys.stderr.flush()
    except Exception:
        pass


# ============================================================================
# CSV
# ============================================================================


def load_csv_rows(
    filename: Path,
) -> list[dict[str, str]]:
    """
    Read all rows from an existing CSV file.
    """

    if not filename.exists():
        return []

    with filename.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        return list(
            csv.DictReader(file)
        )


def update_csv(
    filename: Path,
    repositories: list[dict[str, str]],
    scrape_date: str,
) -> tuple[int, int]:
    """
    Upsert one complete snapshot.

    If scrape_date already exists, all rows for that date are removed
    and replaced with the current scrape.

    Rows belonging to all other dates remain unchanged.
    """

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    if not repositories:
        raise RuntimeError(
            "Scraper returned zero repositories. "
            "Existing CSV data will not be modified."
        )

    filename.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing_rows: list[dict[str, str]] = []

    replaced_rows = 0

    # ------------------------------------------------------------------
    # Existing data
    # ------------------------------------------------------------------

    if filename.exists():
        with filename.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as file:

            reader = csv.DictReader(
                file
            )

            for row in reader:
                if row.get("date") == scrape_date:
                    replaced_rows += 1
                    continue

                existing_rows.append(
                    {
                        field: row.get(
                            field,
                            "",
                        )
                        for field in CSV_FIELDS
                    }
                )

    # ------------------------------------------------------------------
    # Current snapshot
    # ------------------------------------------------------------------

    current_rows = [
        {
            "date": scrape_date,
            "title": repository["title"],
            "desc": repository["desc"],
            "link": repository["link"],
        }
        for repository in repositories
    ]

    rows = (
        existing_rows
        + current_rows
    )

    # ------------------------------------------------------------------
    # Sort by:
    #
    # date  DESC
    # title ASC
    # ------------------------------------------------------------------

    rows.sort(
        key=lambda row: row.get(
            "title",
            "",
        ).casefold()
    )

    rows.sort(
        key=lambda row: row.get(
            "date",
            "",
        ),
        reverse=True,
    )

    # ------------------------------------------------------------------
    # Write CSV
    # ------------------------------------------------------------------

    with filename.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS,
        )

        writer.writeheader()
        writer.writerows(
            rows
        )

    return replaced_rows, len(rows)


# ============================================================================
# Snapshot listing
# ============================================================================


def show_snapshots(
    filename: Path,
) -> int:
    """
    Show all stored snapshots and the number of repositories per snapshot.
    """

    if not filename.exists():
        print(
            f"CSV file does not exist: {filename}",
            file=sys.stderr,
        )
        return 1

    rows = load_csv_rows(
        filename
    )

    if not rows:
        print(
            f"No snapshots in {filename}"
        )
        return 0

    counts = Counter(
        row.get(
            "date",
            "",
        )
        for row in rows
        if row.get("date")
    )

    dates = sorted(
        counts,
        reverse=True,
    )

    print()
    print(
        "GitHub Trending Snapshots"
    )
    print(
        "=" * 42
    )

    print(
        f"{'Date':<20}"
        f"{'Repositories':>14}"
    )

    print(
        "-" * 42
    )

    for snapshot_date in dates:
        print(
            f"{snapshot_date:<20}"
            f"{counts[snapshot_date]:>14}"
        )

    print(
        "-" * 42
    )

    print(
        f"{'Snapshots':<20}"
        f"{len(dates):>14}"
    )

    print(
        f"{'Total rows':<20}"
        f"{len(rows):>14}"
    )

    print()

    return 0


# ============================================================================
# Statistics
# ============================================================================


def show_stats(
    filename: Path,
) -> int:
    """
    Display statistics for the historical GitHub Trending CSV.
    """

    if not filename.exists():
        print(
            f"CSV file does not exist: {filename}",
            file=sys.stderr,
        )
        return 1

    rows = load_csv_rows(
        filename
    )

    if not rows:
        print(
            f"No data in {filename}"
        )
        return 0

    # ------------------------------------------------------------------
    # Repository counts by date
    # ------------------------------------------------------------------

    counts = Counter(
        row.get(
            "date",
            "",
        )
        for row in rows
        if row.get("date")
    )

    dates = sorted(
        counts,
        reverse=True,
    )

    # ------------------------------------------------------------------
    # Unique repositories
    # ------------------------------------------------------------------

    unique_repositories = {
        row.get(
            "link",
            "",
        )
        for row in rows
        if row.get("link")
    }

    # ------------------------------------------------------------------
    # Repository occurrence counts
    # ------------------------------------------------------------------

    repository_counts = Counter(
        row.get(
            "title",
            "",
        )
        for row in rows
        if row.get("title")
    )

    repeated = [
        (
            title,
            count,
        )
        for title, count
        in repository_counts.items()
        if count > 1
    ]

    repeated.sort(
        key=lambda item: (
            -item[1],
            item[0].casefold(),
        )
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    print()
    print(
        "GitHub Trending Statistics"
    )
    print(
        "=" * 70
    )

    print(
        f"File                : {filename}"
    )

    print(
        f"Snapshots           : {len(dates)}"
    )

    print(
        f"Total rows          : {len(rows)}"
    )

    print(
        f"Unique repositories : "
        f"{len(unique_repositories)}"
    )

    if dates:
        print(
            f"Newest snapshot     : {dates[0]}"
        )

        print(
            f"Oldest snapshot     : {dates[-1]}"
        )

        average = (
            len(rows)
            / len(dates)
        )

        print(
            f"Average per snapshot: {average:.1f}"
        )

    # ------------------------------------------------------------------
    # Repository counts
    # ------------------------------------------------------------------

    print()
    print(
        "Repositories per date"
    )
    print(
        "-" * 70
    )

    for snapshot_date in dates:
        print(
            f"{snapshot_date:<15}"
            f"{counts[snapshot_date]:>5}"
        )

    # ------------------------------------------------------------------
    # Repeated repositories
    # ------------------------------------------------------------------

    if repeated:
        print()
        print(
            "Repeated repositories"
        )
        print(
            "-" * 70
        )

        for title, count in repeated:
            print(
                f"{count:>3}x  {title}"
            )

    print()

    return 0


# ============================================================================
# HTTP and optional browser fetching
# ============================================================================


def download_html(url: str) -> str:
    """GitHub Trending is server-rendered; Chrome is normally unnecessary."""
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; github-trending/0.1)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="replace")
    if not html.strip():
        raise RuntimeError("GitHub Trending returned an empty response")
    return html


async def fetch_html_http(url: str = URL, attempts: int = 3) -> str:
    """Retry transient HTTPS failures before starting a browser."""
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(download_html, url)
        except Exception as exc:
            logging.info("HTTPS attempt %d/%d failed: %s", attempt, attempts, exc)
            if attempt == attempts:
                raise
            await asyncio.sleep(attempt)
    raise RuntimeError("HTTPS fetch failed")


async def fetch_html_browser(url: str = URL, attempts: int = 2) -> str:
    """Browser fallback: retry an intermittent DevTools connection failure."""
    try:
        import nodriver as uc
    except ImportError as exc:
        raise RuntimeError(
            "Browser fallback requires nodriver (run: just install)"
        ) from exc

    for attempt in range(1, attempts + 1):
        browser = None
        try:
            options = {"headless": True}
            # Only disable Chrome's sandbox if running as root.
            # Normal macOS cron runs must not require this option.
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                options["no_sandbox"] = True

            browser = await uc.start(**options)
            page = await browser.get(url)
            found = await page.select("article.Box-row", timeout=15)
            if found is None:
                raise RuntimeError("No repository cards appeared in Chrome")
            return await page.get_content()
        except Exception as exc:
            logging.info(
                "Browser attempt %d/%d failed: %s", attempt, attempts, exc,
            )
            if attempt == attempts:
                raise RuntimeError(
                    f"Chrome could not fetch Trending after {attempts} attempts: {exc}"
                ) from exc
            await asyncio.sleep(attempt)
        finally:
            if browser is not None:
                try:
                    browser.stop()
                except Exception:
                    logging.debug("Could not stop Chrome cleanly", exc_info=True)

    raise RuntimeError("Browser fetch failed")


# ============================================================================
# Repository extraction
# ============================================================================


def parse_repositories(html: str) -> list[dict[str, str]]:
    """Extract all cards from one fully received HTML document."""
    articles = Selector(text=html).css("article.Box-row")
    if not articles:
        raise RuntimeError("No GitHub Trending repository cards found")

    repositories = []
    seen = set()

    for article in articles:
        href = article.css("h2 a::attr(href)").get()
        if not href:
            raise RuntimeError("Trending card without a repository link")

        path = href.strip().split("?", 1)[0].strip("/")
        if len(path.split("/")) != 2:
            raise RuntimeError(f"Invalid repository path: {href!r}")
        link = urljoin("https://github.com/", path)
        if link in seen:
            raise RuntimeError(f"Duplicate repository card: {link}")
        seen.add(link)

        parts = article.xpath(
            './/p[contains(concat(" ", normalize-space(@class), " "), '
            '" col-9 ")]//text()'
        ).getall()
        description = " ".join(text.strip() for text in parts if text.strip())

        repositories.append({"title": path, "desc": description, "link": link})

    return repositories


def show_repositories(repositories: list[dict[str, str]]) -> None:
    print("\nRepositories:", flush=True)
    width = max(len(repo["title"]) for repo in repositories)
    for index, repo in enumerate(repositories, 1):
        print(
            f"  {index:>2}. {repo['title']:<{width}}  {repo['link']}",
            flush=True,
        )


# ============================================================================
# Scraper
# ============================================================================


async def scrape(
    output_file: Path,
    *,
    details: bool = False,
    browser: bool = False,
) -> None:
    scrape_date = date.today().isoformat()

    print(f"GitHub Trending | {scrape_date}", flush=True)
    print(f"Source: {URL}\n", flush=True)

    with ProgressStep("[1/3] Fetching trending page...", details=details) as step:
        if browser:
            html = await fetch_html_browser(URL)
            source = "Chrome"
        else:
            try:
                html = await fetch_html_http(URL)
                source = "HTTPS"
            except Exception as exc:
                logging.info("HTTPS fetch failed, using Chrome: %s", exc)
                try:
                    html = await fetch_html_browser(URL)
                except Exception as browser_exc:
                    raise RuntimeError(
                        f"HTTPS fetch failed: {exc}; "
                        f"Chrome fallback failed: {browser_exc}"
                    ) from browser_exc
                source = "Chrome fallback"
        step.status = f"OK ({source})"

    with ProgressStep("[2/3] Parsing repositories...", details=details) as step:
        try:
            repositories = parse_repositories(html)
        except Exception as exc:
            if source != "HTTPS":
                raise
            logging.info("HTTPS returned unusable HTML; using Chrome: %s", exc)
            html = await fetch_html_browser(URL)
            repositories = parse_repositories(html)
            source = "Chrome fallback"
        noun = "repository" if len(repositories) == 1 else "repositories"
        step.status = f"OK ({len(repositories)} {noun} found, {source})"

    show_repositories(repositories)

    with ProgressStep("[3/3] Updating CSV snapshot...", details=details):
        replaced_rows, total_rows = update_csv(
            output_file,
            repositories,
            scrape_date,
        )

    print(flush=True)
    print(
        f"{'Result:':<{STATUS_COLUMN_WIDTH}}"
        f"{len(repositories)} saved | "
        f"{replaced_rows} replaced | "
        f"{total_rows} total records",
        flush=True,
    )
    print(f"CSV file: {output_file}", flush=True)


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    args = parse_args()

    # Configure output before crawler/browser initialization.
    configure_output(
        args
    )

    try:

        # ------------------------------------------------------------------
        # Statistics
        # ------------------------------------------------------------------

        if args.stats:
            return show_stats(
                args.output
            )

        # ------------------------------------------------------------------
        # Snapshots
        # ------------------------------------------------------------------

        if args.snapshots:
            return show_snapshots(
                args.output
            )

        # ------------------------------------------------------------------
        # Log header
        # ------------------------------------------------------------------

        if args.log:
            print()
            print(
                "=" * 70
            )

            print(
                "GitHub Trending scraper started: "
                f"{datetime.now().isoformat(timespec='seconds')}"
            )

            print(
                f"Output: {args.output}"
            )

            print(
                "=" * 70
            )

        # ------------------------------------------------------------------
        # Scrape
        # ------------------------------------------------------------------

        asyncio.run(
            scrape(
                args.output,
                details=args.details,
                browser=args.browser,
            )
        )

        return 0

    except KeyboardInterrupt:
        return 130

    except Exception as exc:
        if args.details:
            logging.exception("GitHub Trending scraper failed")
        else:
            print(f"ERROR: {exc}", file=sys.stderr)

        return 1

    finally:
        flush_output()


# ============================================================================
# Entry point
# ============================================================================


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
