#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting pipeline..."

python3 fetch.py
python3 analyze.py
python3 build_context.py
python3 build_dashboard.py

git add data/articles.db data/context.md docs/
if git diff --staged --quiet; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Nothing new to commit."
else
  git commit -m "chore: update [$(date -u '+%H:%M UTC')]"
  git push
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pushed."
fi
