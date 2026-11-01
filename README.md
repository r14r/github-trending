# GitHub Trending

Collect daily GitHub Trending repositories in a historical CSV.

## Commands

Set up the project using `just setup`, then run:

| Command | Description |
| --- | --- |
| `python github_trending.py` | Scrape with concise progress and final result (default). |
| `python github_trending.py --details` | Include nodriver and Crawlee diagnostic logs. |
| `python github_trending.py --silent` | Suppress all console output. |
| `python github_trending.py --log logs/github_trending.log` | Append status and errors to the specified file instead of the terminal. |
| `python github_trending.py --details --log logs/github_trending.log` | Append full diagnostic output to the specified file. |
| `python github_trending.py --stats` | Show statistics for the saved CSV. |
| `python github_trending.py --snapshots` | List the available dates. |
| `python github_trending.py --output filename.csv` | Choose the CSV path. |
| `just test` | Run regression tests without starting Chrome. |

`just run`, `just details`, `just silent`, and `just run-background` provide corresponding shortcuts.

The CSV records `date,title,desc,link`. Running the scraper again on the same date
replaces that date's snapshot and retains all other dates. Empty scrape results
never overwrite the CSV.

In normal mode the program reports browser startup, scraping, CSV update, and
a summary. Errors are printed without a traceback; use `--details` for
diagnostics. The existing `--silent --log FILE` combination still writes to
the logfile while keeping the terminal quiet.
