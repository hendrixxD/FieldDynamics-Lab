#!/usr/bin/env bash
# Fails if any Python library version is out of step with the pinned requirements files.
# Run it on a clean checkout, with the requirements-dev.txt environment on PATH.
set -euo pipefail

failed=0

check_pins_match_sources() {
  bash .github/scripts/lock-requirements.sh
  if ! git diff --exit-code -- requirements.txt requirements-dev.txt; then
    echo "The requirements files are out of date or hand-edited."
    echo "Run bash .github/scripts/lock-requirements.sh and commit the result."
    failed=1
  fi
}

check_environment_matches_pins() {
  local plan
  # --python targets the active interpreter, which is not a virtualenv on the CI runner.
  plan=$(uv pip install --dry-run --python "$(command -v python)" -r requirements-dev.txt 2>&1)
  if [[ "$plan" != *"Would make no changes"* ]]; then
    echo "$plan"
    echo "The installed packages differ from requirements-dev.txt."
    failed=1
  fi
}

check_no_conflicts() {
  if ! uv pip check --python "$(command -v python)"; then
    echo "The installed packages conflict with each other."
    failed=1
  fi
}

# pre-commit installs these tools itself, so their versions can drift from the pinned ones.
check_hook_version() {
  local package=$1 hook_repo=$2 pinned hook
  pinned=$(grep -oP "^${package}==\K.+" requirements-dev.txt)
  hook=$(grep -A1 "repo: ${hook_repo}" .pre-commit-config.yaml | grep -oP 'rev: v\K.+')
  if [[ "$pinned" != "$hook" ]]; then
    echo "${package} is ${pinned} in requirements-dev.txt but ${hook} in .pre-commit-config.yaml."
    failed=1
  fi
}

check_pins_match_sources
check_environment_matches_pins
check_no_conflicts
check_hook_version ruff https://github.com/astral-sh/ruff-pre-commit
check_hook_version mypy https://github.com/pre-commit/mirrors-mypy

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

echo "All Python library versions match the pinned requirements files."
