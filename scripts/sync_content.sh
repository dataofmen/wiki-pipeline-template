#!/bin/bash
# wiki -> Quartz content synchronization
# Templated version — customize paths and folders as needed
set -e
WIKI_ROOT="$(dirname "$0")/.."
QUARTZ_CONTENT="$WIKI_ROOT/quartz/content"

if [ ! -d "$QUARTZ_CONTENT" ]; then
  echo "Quartz not installed. Run 'npx quartz create' first."
  exit 1
fi

for folder in wiki garden clippings; do
  if [ -d "$WIKI_ROOT/$folder" ]; then
    rsync -av --delete "$WIKI_ROOT/$folder/" "$QUARTZ_CONTENT/$folder/"
  else
    echo "Warning: $WIKI_ROOT/$folder not found, skipping."
  fi
done

if [ -f "$WIKI_ROOT/index.md" ]; then
  cp "$WIKI_ROOT/index.md" "$QUARTZ_CONTENT/index.md"
fi

echo "Sync completed: $(date)"