#!/bin/bash
# wiki → Quartz content 동기화
set -e
WIKI_ROOT="$(dirname "$0")/.."
QUARTZ_CONTENT="$WIKI_ROOT/quartz/content"

if [ ! -d "$QUARTZ_CONTENT" ]; then
  echo "Quartz 미설치. 먼저 quartz 폴더를 설정하세요."
  exit 1
fi

echo "YAML 프론트매터 타이틀 수정 중..."
python3 "$WIKI_ROOT/scripts/fix_yaml_titles.py"

echo "노트 디렉토리 위치 보정 중..."
python3 "$WIKI_ROOT/scripts/fix_paths.py"

# Public /wiki must expose only two child folders:
#   /wiki/entities/  — people, tools, orgs, frameworks
#   /wiki/concepts/  — every other machine-readable wiki note
# Do not sync arbitrary files or folders directly under wiki/ (e.g. legacy
# wiki/*.md or wiki/events/), because Quartz will publish them as additional
# top-level wiki sections.
rm -rf "$QUARTZ_CONTENT/wiki"
mkdir -p "$QUARTZ_CONTENT/wiki/concepts" "$QUARTZ_CONTENT/wiki/entities"
rsync -av --exclude="_quarantine" --delete "$WIKI_ROOT/wiki/concepts/" "$QUARTZ_CONTENT/wiki/concepts/"
rsync -av --exclude="_quarantine" --delete "$WIKI_ROOT/wiki/entities/" "$QUARTZ_CONTENT/wiki/entities/"

for folder in garden clippings; do
  if [ "$folder" = "clippings" ]; then
    rsync -av --exclude="_quarantine" --exclude="*/" --delete "$WIKI_ROOT/$folder/" "$QUARTZ_CONTENT/$folder/"
  else
    rsync -av --exclude="_quarantine" --delete "$WIKI_ROOT/$folder/" "$QUARTZ_CONTENT/$folder/"
  fi
done

cp "$WIKI_ROOT/index.md" "$QUARTZ_CONTENT/index.md"
echo "동기화 완료: $(date)"
