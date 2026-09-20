#!/bin/bash
set -eo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Run the normal unit/regression suite with branch coverage.
poetry run pytest -vvvv --cov=heckle --cov-branch --cov-report=term-missing "$@"

# Exercise the installed CLI entry point like a user, without credentials/network.
poetry run heckle --version
poetry run heckle --help >/dev/null
for forge in github gitlab gitea forgejo; do
  poetry run heckle generate "$forge" --help >/dev/null
  poetry run heckle inventory "$forge" --help >/dev/null
done
