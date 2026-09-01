#!/usr/bin/env bash
set -euo pipefail

uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src tests

# tests must not depend on this machine's git config — CI has none
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
    uv run pytest --cov --cov-fail-under=100 tests/ docs/ agent-docs/

# last, and from clean: conf.py sets nitpicky, so a reference to something that no longer
# exists fails here rather than on a pull request
make -C docs clean html SPHINXOPTS=--fail-on-warning
