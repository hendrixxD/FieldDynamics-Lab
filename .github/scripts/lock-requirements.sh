#!/usr/bin/env bash
# Regenerates the requirements files from the .in files. Existing pins are kept. Extra arguments
# go to uv, for example --upgrade or --upgrade-package pandas.
set -euo pipefail

# Compromised releases are usually caught within days, so new versions wait out this period.
COOLDOWN_DAYS=7
cutoff=$(date -u -d "${COOLDOWN_DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ)

# Security fixes listed here skip the wait for that package.
exempt_args=()
while read -r package; do
  exempt_args+=(--exclude-newer-package "${package}=$(date -u +%Y-%m-%dT%H:%M:%SZ)")
done < <(grep -oP '^[a-z0-9._-]+(?===)' .github/cooldown-exempt.txt || true)

lock() {
  local source_file=$1 output_file=$2
  shift 2
  uv pip compile "$source_file" \
    --output-file "$output_file" \
    --python-version 3.12 \
    --python-platform linux \
    --generate-hashes \
    --exclude-newer "$cutoff" \
    "${exempt_args[@]}" \
    --custom-compile-command "bash .github/scripts/lock-requirements.sh" \
    --quiet \
    "$@"
}

lock requirements.in requirements.txt "$@"
lock requirements-dev.in requirements-dev.txt "$@"
# Constrained by requirements.txt, so the deployed app runs the versions CI tested.
lock requirements-serve.in requirements-serve.txt --constraint requirements.txt "$@"
