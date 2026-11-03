#!/usr/bin/env bash
# Fails if any Python library version is out of step with the pinned requirements files.
# Run it on a clean checkout, with the requirements-dev.txt environment on PATH.
set -euo pipefail

failed=0

lock_files=(requirements.txt requirements-dev.txt requirements-serve.txt)

pins() {
  grep -hE '^[a-zA-Z0-9].*==' "${lock_files[@]}"
}

# Compares pins, not hashes: a project can upload a new wheel for an old version at any time.
# The security scan checks each hash against PyPI.
check_pins_match_sources() {
  local before after
  before=$(pins)
  bash .github/scripts/lock-requirements.sh
  after=$(pins)
  if [[ "$before" != "$after" ]]; then
    diff <(echo "$before") <(echo "$after") || true
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
  pinned=$(grep -oP "^${package}==\K[^ ]+" requirements-dev.txt)
  hook=$(grep -A1 "repo: ${hook_repo}" .pre-commit-config.yaml | grep -oP 'frozen: v\K.+')
  if [[ "$pinned" != "$hook" ]]; then
    echo "${package} is ${pinned} in requirements-dev.txt but ${hook} in .pre-commit-config.yaml."
    failed=1
  fi
}

check_hook_dependency() {
  local package=$1 pinned
  pinned=$(grep -oP "^${package}==[^ ]+" requirements-dev.txt)
  if ! grep -qF "$pinned" .pre-commit-config.yaml; then
    echo "${pinned} is missing from additional_dependencies in .pre-commit-config.yaml."
    failed=1
  fi
}

check_pins_match_sources
check_environment_matches_pins
check_no_conflicts
check_hook_version ruff https://github.com/astral-sh/ruff-pre-commit
check_hook_version mypy https://github.com/pre-commit/mirrors-mypy
check_hook_dependency fastapi
check_hook_dependency pydantic

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

echo "All Python library versions match the pinned requirements files."
