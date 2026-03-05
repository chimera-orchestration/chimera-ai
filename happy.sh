#!/usr/bin/env bash
set -euo pipefail

uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src
uv run pytest --cov=chimera --cov=tests --cov-fail-under=100 tests/
