#!/usr/bin/env bash
set -euo pipefail

git add data/race_results.json

if git diff --cached --quiet; then
  echo "No imported data changes; skipping commit and push."
  exit 0
else
  diff_status=$?
  if [ "$diff_status" -ne 1 ]; then
    echo "Unable to inspect staged import changes." >&2
    exit "$diff_status"
  fi
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -m "Import JRA race results"
# The remote main branch may advance while OCR is running. Rebase the
# import commit before pushing; conflicts fail safely without force-pushing.
git pull --rebase origin main
git push
