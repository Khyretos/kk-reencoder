#!/bin/sh
# run tests/ui/check.mjs in the Playwright image against BASE (default the local preview on 127.0.0.1:18080)
D=$(cd "$(dirname "$0")" && pwd)
NM=${NODE_MODULES:-$HOME/Docker/Personal-projects/kreative-kompanion/web/node_modules}
# the static files are mounted into the preview, so a check sees the current draft
exec timeout 90 docker run --rm --network host --memory 1g --cpus 1 -e BASE="${BASE:-http://127.0.0.1:18080}" \
  -v "$D/check.mjs":/ui/check.mjs:ro -v "$NM":/ui/node_modules:ro -w /ui mcr.microsoft.com/playwright:v1.63.0-noble node check.mjs
