#!/bin/sh
# Runs the test suite in a python:3.13 container (no local Python needed).
# Usage (Git Bash / Linux / macOS):  sh run_tests.sh [pytest args]
set -e
cd "$(dirname "$0")"
SRC="$(pwd -W 2>/dev/null || pwd)"
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$SRC":/src:ro \
  -v ha-pip-cache:/root/.cache/pip \
  python:3.13 sh -c 'cp -r /src /work && cd /work && find . -name __pycache__ -prune -exec rm -rf {} + && pip install -q --root-user-action=ignore --disable-pip-version-check -r requirements_test.txt && python -m pytest -p no:cacheprovider "$@"' sh "$@"
