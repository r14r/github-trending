set shell := ["bash", "-cu"]

PYTHON := ".venv/bin/python"

SCRIPT := "./github_trending.py"

OUTPUT := "github_trending.csv"
LOG := "logs/github_trending.log"


# ----------------------------------------------------------------------
# Help
# ----------------------------------------------------------------------

default:
    @just --list


# ----------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------

# Create Python 3.14 virtual environment
venv:
    uv venv --python 3.14


# Install runtime dependencies
install: venv
    uv pip install \
        --python {{PYTHON}} \
        nodriver \
        parsel \
        crawlee


# Install development tools
install-dev: install
    uv pip install \
        --python {{PYTHON}} \
        ruff \
        pytest


# Complete setup
setup: install-dev
    mkdir -p logs data
    @{{PYTHON}} --version


# ----------------------------------------------------------------------
# Scraper
# ----------------------------------------------------------------------

# Run scraper
run:
    @{{PYTHON}} {{SCRIPT}}


# Show browser and crawler diagnostics
details:
    @{{PYTHON}} {{SCRIPT}} --details


# Silent run
silent:
    {{PYTHON}} {{SCRIPT}} --silent


# Run with logfile
log:
    mkdir -p logs
    {{PYTHON}} {{SCRIPT}} \
        --log {{LOG}}


# Scheduled/background execution
run-background:
    @mkdir -p logs
    @{{PYTHON}} {{SCRIPT}} --silent  --log {{LOG}}


# Custom output
run-output output=OUTPUT:
    {{PYTHON}} {{SCRIPT}} \
        --output {{output}}


# Full production-style run
run-full output=OUTPUT log=LOG:
    mkdir -p "$(dirname "{{output}}")"
    mkdir -p "$(dirname "{{log}}")"

    {{PYTHON}} {{SCRIPT}} \
        --silent \
        --log {{log}} \
        --output {{output}}


# ----------------------------------------------------------------------
# Data information
# ----------------------------------------------------------------------

# Detailed statistics
stats:
    {{PYTHON}} {{SCRIPT}} \
        --stats \
        --output {{OUTPUT}}


# Statistics for another CSV
stats-file file:
    {{PYTHON}} {{SCRIPT}} \
        --stats \
        --output {{file}}


# List available snapshots
snapshots:
    {{PYTHON}} {{SCRIPT}} \
        --snapshots \
        --output {{OUTPUT}}


# List snapshots from another CSV
snapshots-file file:
    {{PYTHON}} {{SCRIPT}} \
        --snapshots \
        --output {{file}}


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------

# Show CSV
show:
    @if [ -f "{{OUTPUT}}" ]; then \
        column -s, -t < "{{OUTPUT}}" | less -S; \
    else \
        echo "{{OUTPUT}} does not exist"; \
    fi


# First 20 rows
head:
    @head -20 {{OUTPUT}}


# Last 20 rows
tail:
    @tail -20 {{OUTPUT}}


# ----------------------------------------------------------------------
# Logs
# ----------------------------------------------------------------------

show-log:
    @if [ -f "{{LOG}}" ]; then \
        less +G {{LOG}}; \
    else \
        echo "{{LOG}} does not exist"; \
    fi


tail-log:
    mkdir -p logs
    touch {{LOG}}
    tail -f {{LOG}}


clean-log:
    rm -f {{LOG}}


# ----------------------------------------------------------------------
# Development
# ----------------------------------------------------------------------

check:
    {{PYTHON}} -m py_compile github_trending.py


# Run regression tests (no browser or network required)
test:
    {{PYTHON}} -m unittest discover -s tests -p "test_*.py"


lint:
    .venv/bin/ruff check .


lint-fix:
    .venv/bin/ruff check --fix .


format:
    .venv/bin/ruff format .


format-check:
    .venv/bin/ruff format --check .


quality: check lint format-check


# ----------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------

test-imports:
    {{PYTHON}} -c \
        "import nodriver, parsel, crawlee; print('All imports OK')"


versions:
    @{{PYTHON}} --version
    @echo
    @uv pip list --python {{PYTHON}} \
        | grep -E "nodriver|parsel|crawlee|ruff|pytest"


# ----------------------------------------------------------------------
# Cleanup
# ----------------------------------------------------------------------

clean:
    rm -f {{OUTPUT}}
    rm -rf logs


clean-cache:
    find . \
        -type d \
        -name "__pycache__" \
        -prune \
        -exec rm -rf {} +

    rm -rf \
        .pytest_cache \
        .ruff_cache


clean-venv:
    rm -rf .venv


clean-all: clean clean-cache clean-venv


# ----------------------------------------------------------------------
# Scheduling
# ----------------------------------------------------------------------

cron-example:
    @echo "30 7 * * * cd $(pwd) && just run-background"
