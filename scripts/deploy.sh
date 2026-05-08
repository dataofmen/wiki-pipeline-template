#!/bin/bash
# Quartz build & GitHub Pages deployment
# Templated version — customize paths and commands as needed
set -e
WIKI_ROOT="$(dirname "$0")/.."
QUARTZ_DIR="$WIKI_ROOT/quartz"

if [ ! -d "$QUARTZ_DIR" ]; then
  echo "Error: quartz/ directory not found. Install Quartz first."
  exit 1
fi

echo "[1/3] Syncing content..."
bash "$WIKI_ROOT/scripts/sync_content.sh"

echo "[2/3] Checking broken links..."
python3 "$WIKI_ROOT/scripts/check_links.py" || true

echo "[3/3] Quartz build & deploy..."
cd "$QUARTZ_DIR"
npx quartz sync

echo "Deploy completed: $(date)"