#!/usr/bin/env bash
# Fails if env files or raw data are tracked now or were ever committed.
# .gitignore alone cannot prove this: a tracked file stays tracked after it is ignored.
set -euo pipefail

forbidden_pathspecs=(
  ':(glob)**/.env'
  ':(glob)**/.env.*'
  ':(glob)data/raw/**'
  ':(exclude,glob)**/.env.example'
)

tracked=$(git ls-files -- "${forbidden_pathspecs[@]}")
in_history=$(git log --all --format='%h' -- "${forbidden_pathspecs[@]}")

if [[ -n "$tracked" ]]; then
  echo "These files must not be tracked:"
  echo "$tracked"
fi

if [[ -n "$in_history" ]]; then
  echo "Env files or raw data appear in these commits:"
  echo "$in_history"
fi

if [[ -n "$tracked" || -n "$in_history" ]]; then
  exit 1
fi

echo "No env files or raw data are tracked or in history."
