#!/bin/bash
# Quartz build & GitHub Pages deployment
set -e
WIKI_ROOT="$(dirname "$0")/.."
QUARTZ_DIR="$WIKI_ROOT/quartz"

if [ ! -d "$QUARTZ_DIR" ]; then
  echo "Error: quartz/ directory not found. Install Quartz first."
  exit 1
fi

echo "[1/4] Committing content changes..."
cd "$WIKI_ROOT"
git add clippings/ wiki/ garden/ index.md log.md
if ! git diff --cached --quiet; then
  git commit -m "Pipeline: content update $(date '+%Y-%m-%d %H:%M')"
  git push origin main
  echo "Content changes committed and pushed."
else
  echo "No content changes to commit."
fi

echo "[2/4] Syncing content..."
bash "$WIKI_ROOT/scripts/sync_content.sh"

echo "[3/4] Checking broken links..."
python3 "$WIKI_ROOT/scripts/check_links.py" || true

echo "[4/4] Quartz build & deploy..."
cd "$QUARTZ_DIR"
npx quartz sync

echo "Deploy completed: $(date)"