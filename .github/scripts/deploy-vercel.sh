#!/usr/bin/env bash
# Deploys ./source to Vercel with the pinned CLI and writes the URL to the step output.
# The token is read from VERCEL_TOKEN in the environment, so it never appears on a command line.
set -euo pipefail

# Vercel installs requirements.txt, and the deployed app only needs the small hashed set.
cp source/requirements-serve.txt source/requirements.txt

# The target is always explicit, because the CLI can otherwise pick production.
target=preview
if [[ "$PRODUCTION" == "true" ]]; then
  target=production
fi

output=$(.github/deploy/node_modules/.bin/vercel deploy source --yes --target "$target")
# The CLI prints a bare URL or JSON depending on how it detects its caller.
url=$(grep -oE 'https://[a-z0-9-]+\.vercel\.app' <<<"$output" | head -1)
if [[ -z "$url" ]]; then
  echo "The Vercel CLI returned no deployment URL."
  exit 1
fi

# Vercel reports Ready even when the app cannot serve, so test the app itself.
curl --fail --silent --show-error --retry 3 --retry-all-errors --max-time 60 \
  "${url}/api/health" >/dev/null

echo "url=${url}" >>"$GITHUB_OUTPUT"
echo "Deployed ${url}"
