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

for folder in wiki garden clippings; do
  rsync -av --delete "$WIKI_ROOT/$folder/" "$QUARTZ_CONTENT/$folder/"
done

cp "$WIKI_ROOT/index.md" "$QUARTZ_CONTENT/index.md"
echo "동기화 완료: $(date)"
