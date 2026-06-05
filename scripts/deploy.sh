#!/bin/bash
# Quartz 빌드 & GitHub Pages 배포 (pipeline.py 로 통합됨)
set -euo pipefail

WIKI_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "pipeline.py --phase 5 를 실행합니다..."
exec python3 "$WIKI_ROOT/scripts/pipeline.py" --phase 5