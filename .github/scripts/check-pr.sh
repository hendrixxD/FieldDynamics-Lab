#!/usr/bin/env bash
# Fails if a PR breaks the branch naming or template rules in AGENTS.md.
# Reads PR_HEAD, PR_BASE, and PR_BODY from the environment. The workflow passes them that way
# because a branch name or PR body placed directly in a shell command could run as code.
set -euo pipefail

failed=0

fail() {
  echo "$1"
  failed=1
}

ticked_count() {
  grep -cE "^- \[[xX]\] ($1)\b" <<<"$PR_BODY" || true
}

check_flow_pr() {
  local allowed_base=$1
  if [[ "$PR_BASE" != "$allowed_base" ]]; then
    fail "A PR from ${PR_HEAD} must target ${allowed_base}, not ${PR_BASE}."
  fi
}

check_template() {
  local expected_type=$1 type_count impact_count
  type_count=$(ticked_count 'Fix|New feature')
  impact_count=$(ticked_count 'Low|Medium|High')

  if [[ "$type_count" -ne 1 ]]; then
    fail "Tick exactly one type of change. Found ${type_count}."
  elif [[ "$(ticked_count "$expected_type")" -ne 1 ]]; then
    fail "Branch ${PR_HEAD} must tick the type of change: ${expected_type}."
  fi

  if [[ "$impact_count" -ne 1 ]]; then
    fail "Tick exactly one level of impact. Found ${impact_count}."
  fi
}

case "$PR_HEAD" in
  staging) check_flow_pr main ;;
  main) check_flow_pr staging ;;
  *)
    if [[ ! "$PR_HEAD" =~ ^(feat|fix)-[a-z0-9]+([-_][a-z0-9]+)*$ ]]; then
      fail "Branch ${PR_HEAD} must be named feat-name_of_feat or fix-name_of_fix."
    fi
    check_flow_pr staging
    if [[ "$PR_HEAD" == fix-* ]]; then
      check_template 'Fix'
    else
      check_template 'New feature'
    fi
    ;;
esac

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

echo "PR follows the branch and template rules."
