# GitHub Trending

Collect GitHub Trending repositories in a historical CSV, with one snapshot
per calendar day. The scraper fetches the server-rendered HTML directly via
HTTPS; Chrome (nodriver) is only used if HTTPS fails, returns unusable HTML,
or when explicitly requested with `--browser`. Crawlee is no longer required.

## Setup

```bash
just setup
just run
```

`just run` uses the project's `.venv/bin/python`; no manual activation is
required, including in cron jobs.

## Commands

| Command | Description |
| --- | --- |
| `just run` | Fetch Trending and show source URL, progress, every repository URL, and CSV results. |
| `just details` | Include detailed scraper/Chrome diagnostics. |
| `just browser` | Force nodriver/Chrome (normally unnecessary). |
| `just silent` | Suppress all console output. |
| `just log` | Append console output to `logs/github_trending.log`. |
| `just run-background` | Quiet terminal; append output to `logs/github_trending.log`. |
| `just test` | Run regression tests, no Chrome or network. |
| `just stats` | Show historical statistics. |
| `just snapshots` | Show available snapshot dates. |

CLI flags also include `--details`, `--browser`, `--silent`,
`--log FILE`, `--output FILE`, `--stats`, and `--snapshots`.

## Example

```text
GitHub Trending | 2026-11-01
Source: https://github.com/trending

[1/3] Fetching trending page...               OK (HTTPS)
[2/3] Parsing repositories...                 OK (24 repositories found, HTTPS)

Repositories:
   1. ripienaar/free-for-dev  https://github.com/ripienaar/free-for-dev
   2. electron/electron       https://github.com/electron/electron
   ...
  24. microsoft/playwright   https://github.com/microsoft/playwright
[3/3] Updating CSV snapshot...                OK

Result:                                       24 saved | 14 replaced | 456 total records
CSV file: github_trending.csv
```

Counts are illustrative, not guarantees: GitHub Trending changes throughout the
day. GitHub may also return different results from different requests or sessions.

## Browser errors

A transient `Failed to connect to browser` error originates from
nodriver/Chrome's DevTools connection. The normal HTTPS path avoids Chrome.
If HTTPS cannot return parseable Trending cards, the scraper retries Chrome
startup twice, using a new browser session for each attempt. Run
`just details` to see retry diagnostics or `just browser` to reproduce
a browser-only problem.

The CSV has fields `date,title,desc,link`. Re-running the scraper on
the same date replaces that day's snapshot, but preserves other dates.
If fetching or parsing fails, no existing CSV rows are modified.
