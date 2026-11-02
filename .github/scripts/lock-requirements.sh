#!/usr/bin/env bash
# Regenerates requirements.txt and requirements-dev.txt from the .in files. Existing pins are
# kept. Extra arguments go to uv, for example --upgrade or --upgrade-package pandas.
set -euo pipefail

lock() {
  local source_file=$1 output_file=$2
  shift 2
  uv pip compile "$source_file" \
    --output-file "$output_file" \
    --python-version 3.11 \
    --python-platform linux \
    --custom-compile-command "bash .github/scripts/lock-requirements.sh" \
    --quiet \
    "$@"
}

lock requirements.in requirements.txt "$@"
lock requirements-dev.in requirements-dev.txt "$@"
