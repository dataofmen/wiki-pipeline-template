#!/bin/bash
# Quartz 빌드 & GitHub Pages 배포
set -euo pipefail

WIKI_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QUARTZ_DIR="$WIKI_ROOT/quartz"
SCRIPTS_DIR="$WIKI_ROOT/scripts"
DEPLOY_REPO="$WIKI_ROOT/../dataofmen.github.io"

if [ ! -d "$QUARTZ_DIR" ]; then
  echo "오류: quartz/ 디렉토리 없음. 먼저 Quartz를 설치하세요."
  exit 1
fi

echo "[0/4] 0004_Wikis GitHub 동기화..."
cd "$WIKI_ROOT"
git add wiki/ garden/ clippings/ scripts/ index.md 2>/dev/null || true
if ! git diff --cached --quiet; then
  ts=$(date -u +%Y%m%d-%H%M%S)
  git commit -m "content: deploy auto-push $ts" || true
fi
git push origin main 2>/dev/null || echo "  (push 없음 또는 이미 최신)"

echo "[1/4] 콘텐츠 동기화..."
bash "$SCRIPTS_DIR/sync_content.sh"

echo "[2/4] 링크 검사..."
python3 "$SCRIPTS_DIR/check_links.py" --strict || {
  echo "WARNING: 깨진 링크 발견. 10초 후 계속... (Ctrl+C로 중단)"
  sleep 10
}

echo "[3/4] Quartz 빌드..."
cd "$QUARTZ_DIR"
npx quartz build

echo "[4/4] GitHub Pages 배포..."
if [ ! -d "$DEPLOY_REPO/.git" ]; then
  echo "클론 중: dataofmen.github.io..."
  cd "$WIKI_ROOT/.."
  git clone https://github.com/dataofmen/dataofmen.github.io.git
fi

rsync -avz --delete --exclude='.git' "$QUARTZ_DIR/public/" "$DEPLOY_REPO/"
cd "$DEPLOY_REPO"
git add -A
git commit -m "deploy: $(date -u +%Y%m%d-%H%M%S)" || true
git push origin main

echo "배포 완료: $(date)"