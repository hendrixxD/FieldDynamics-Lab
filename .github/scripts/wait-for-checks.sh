#!/usr/bin/env bash
# Fails unless the checks that run outside the CI workflow pass for HEAD_SHA. Deploy starts when
# CI ends, and these may still be running, so it polls instead of reading once.
set -euo pipefail

REQUIRED_CHECKS=("Security gate" "PR format")
POLL_ATTEMPTS=20
POLL_SECONDS=15

conclusions() {
  local name=$1
  gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}/check-runs" --paginate \
    --jq ".check_runs[] | select(.name == \"${name}\") | .conclusion // \"pending\""
}

all_passed() {
  local name result
  for name in "${REQUIRED_CHECKS[@]}"; do
    result=$(conclusions "$name")
    if grep -qvE '^(success|pending)$' <<<"$result" && [[ -n "$result" ]]; then
      echo "Blocking deployment: ${name} did not pass."
      exit 1
    fi
    if [[ -z "$result" ]] || grep -q pending <<<"$result"; then
      return 1
    fi
  done
}

for ((attempt = 1; attempt <= POLL_ATTEMPTS; attempt++)); do
  if all_passed; then
    echo "All required checks passed for ${HEAD_SHA}."
    exit 0
  fi
  sleep "$POLL_SECONDS"
done

echo "Blocking deployment: required checks did not finish in time."
exit 1
