#!/usr/bin/env python3

import argparse
import csv
import logging
import os
import sys

from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import nodriver as uc

from parsel import Selector

from crawlee import ConcurrencySettings
from crawlee.crawlers import (
    BasicCrawler,
    BasicCrawlingContext,
)


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

STATUS_COLUMN_WIDTH = 48


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
        help="Show detailed nodriver and Crawlee diagnostic logs.",
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
# Scraper
# ============================================================================


async def scrape(
    output_file: Path,
    *,
    details: bool = False,
) -> None:
    scrape_date = date.today().isoformat()

    repositories: list[
        dict[str, str]
    ] = []

    browser = None

    try:

        # ------------------------------------------------------------------
        # Start Chromium through nodriver
        # ------------------------------------------------------------------

        with ProgressStep("[1/3] Starting browser...", details=details):
            browser = await uc.start(
                headless=True,
            )

        # ------------------------------------------------------------------
        # Crawlee
        # ------------------------------------------------------------------

        crawler = BasicCrawler(
            max_requests_per_crawl=1,
            max_request_retries=3,
            concurrency_settings=ConcurrencySettings(
                desired_concurrency=1,
                max_concurrency=1,
            ),
        )

        # ------------------------------------------------------------------
        # Request handler
        # ------------------------------------------------------------------

        @crawler.router.default_handler
        async def handler(
            context: BasicCrawlingContext,
        ) -> None:

            url = str(
                context.request.url
            )

            context.log.info(
                f"Opening {url}"
            )

            # --------------------------------------------------------------
            # Render page with nodriver
            # --------------------------------------------------------------

            page = await browser.get(
                url
            )

            # --------------------------------------------------------------
            # Wait for repository cards
            # --------------------------------------------------------------

            repository_element = (
                await page.select(
                    "article.Box-row",
                    timeout=15,
                )
            )

            if repository_element is None:
                raise RuntimeError(
                    "No repository elements found "
                    "on GitHub Trending."
                )

            # --------------------------------------------------------------
            # Rendered HTML
            # --------------------------------------------------------------

            html = (
                await page.get_content()
            )

            # --------------------------------------------------------------
            # Parsel
            # --------------------------------------------------------------

            selector = Selector(
                text=html
            )

            articles = selector.css(
                "article.Box-row"
            )

            context.log.info(
                f"Found {len(articles)} "
                "repository elements"
            )

            # --------------------------------------------------------------
            # Extract repositories
            # --------------------------------------------------------------

            for article in articles:

                href = article.css(
                    "h2 a::attr(href)"
                ).get()

                if not href:
                    continue

                href = href.strip()

                title = (
                    href.strip("/")
                )

                # ----------------------------------------------------------
                # Description
                # ----------------------------------------------------------

                description_parts = (
                    article.xpath(
                        './/p['
                        'contains(@class, "col-9")'
                        ']//text()'
                    ).getall()
                )

                description = " ".join(
                    text.strip()
                    for text
                    in description_parts
                    if text.strip()
                )

                # ----------------------------------------------------------
                # Absolute link
                # ----------------------------------------------------------

                link = urljoin(
                    "https://github.com",
                    href,
                )

                repository = {
                    "title": title,
                    "desc": description,
                    "link": link,
                }

                repositories.append(
                    repository
                )

                # ----------------------------------------------------------
                # Crawlee dataset
                # ----------------------------------------------------------

                await context.push_data(
                    {
                        "date": scrape_date,
                        **repository,
                    }
                )

        # ------------------------------------------------------------------
        # Run crawler
        # ------------------------------------------------------------------

        with ProgressStep(
            "[2/3] Fetching trending repositories...", details=details,
        ) as step:
            await crawler.run([URL])

            # Never overwrite a previous snapshot after an empty scrape.
            if not repositories:
                raise RuntimeError(
                    "No repositories were extracted. "
                    "CSV was not modified."
                )
            step.status = f"OK ({len(repositories)} repositories found)"

        # ------------------------------------------------------------------
        # Update CSV
        # ------------------------------------------------------------------

        with ProgressStep(
            "[3/3] Updating CSV snapshot...", details=details,
        ):
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

    finally:

        # ------------------------------------------------------------------
        # Stop browser
        # ------------------------------------------------------------------

        if browser is not None:
            browser.stop()


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

        uc.loop().run_until_complete(
            scrape(
                args.output,
                details=args.details,
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
