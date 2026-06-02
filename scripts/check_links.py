"""
위키링크 broken link 검사 스크립트
Quartz의 "shortest" 해상도 전략을 시뮬레이션합니다.

- path-prefixed 링크 ([[wiki/concepts/foo]]): vault 루트에서 해석
- bare slug ([[foo]]): 전역 slug 인덱스에서 유일 매칭 검사
  - 0개 매칭: broken
  - 1개 매칭: 정상
  - 2개 이상 매칭: ambiguous (경고)

사용법: python3 check_links.py [--root /path/to/wiki] [--strict]
"""
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
WIKI_ROOT = Path(__file__).parent.parent

# 검사에서 제외할 디렉토리
EXCLUDE_DIRS = {"_workspace", "_workspace_prev", "inbox", "raw", ".claude", ".obsidian", ".omc"}


# 소스 디렉토리만 인덱싱 (quartz/content/ 등 복사본 제외)
SOURCE_DIRS = ["wiki", "garden", "clippings"]


def build_slug_index(root: Path) -> dict[str, list[str]]:
    """파일명(stem) → 전체 상대경로 목록 매핑. 동일 파일명이 여러 디렉토리에 있으면 ambiguous."""
    index: dict[str, list[str]] = defaultdict(list)
    for src in SOURCE_DIRS:
        src_dir = root / src
        if not src_dir.exists():
            continue
        for md in src_dir.rglob("*.md"):
            rel = str(md.relative_to(root).with_suffix(""))
            stem = md.stem
            index[stem].append(rel)
    return index


def build_path_set(root: Path) -> set[str]:
    """소스 디렉토리의 전체 상대경로(확장자 없음) 집합. path-prefixed 링크 검증용."""
    paths = set()
    for src in SOURCE_DIRS:
        src_dir = root / src
        if not src_dir.exists():
            continue
        for md in src_dir.rglob("*.md"):
            paths.add(str(md.relative_to(root).with_suffix("")))
    return paths


def check_links(root: Path) -> tuple[list[dict], list[dict]]:
    """shortest 해상도 시뮬레이션으로 링크 검사."""
    slug_index = build_slug_index(root)
    path_set = build_path_set(root)
    broken = []
    ambiguous = []

    search_dirs = [root / "wiki", root / "garden", root / "clippings"]
    for d in search_dirs:
        if not d.exists():
            continue
        for md in d.rglob("*.md"):
            content = md.read_text(encoding="utf-8", errors="replace")
            for match in WIKILINK_RE.finditer(content):
                target = match.group(1).strip()
                target_clean = target.replace("\\", "/")
                if target_clean.endswith(".md"):
                    target_clean = target_clean[:-3]

                # index.md는 스킵
                if target_clean.endswith("/index") or target_clean == "index":
                    continue

                if "/" in target_clean:
                    # path-prefixed 링크: vault 루트에서 해석
                    if target_clean not in path_set:
                        broken.append({
                            "file": str(md.relative_to(root)),
                            "link": target,
                            "reason": f"path-prefixed target not found: {target_clean}",
                        })
                else:
                    # bare slug: shortest 해상도 시뮬레이션
                    matches = slug_index.get(target_clean, [])
                    if len(matches) == 0:
                        broken.append({
                            "file": str(md.relative_to(root)),
                            "link": target,
                            "reason": "no matching file found",
                        })
                    elif len(matches) > 1:
                        ambiguous.append({
                            "file": str(md.relative_to(root)),
                            "link": target,
                            "reason": f"ambiguous: {len(matches)} files match: {matches}",
                        })
                    # len(matches) == 1: 정상 (unique slug)

    return broken, ambiguous


def main():
    parser = argparse.ArgumentParser(description="위키링크 broken link 검사 (shortest 해상도 시뮬레이션)")
    parser.add_argument("--root", default=str(WIKI_ROOT), help="wiki 루트 디렉토리")
    parser.add_argument("--strict", action="store_true", help="broken link 있으면 exit code 1")
    args = parser.parse_args()

    root = Path(args.root)
    broken, ambiguous = check_links(root)

    if not broken and not ambiguous:
        print("✓ broken link 없음, ambiguous link 없음")
        sys.exit(0)

    if broken:
        print(f"⚠ broken link {len(broken)}개 발견:\n")
        for item in broken:
            print(f"  {item['file']}: [[{item['link']}]] — {item['reason']}")

    if ambiguous:
        print(f"\n⚠ ambiguous link {len(ambiguous)}개 발견:\n")
        for item in ambiguous:
            print(f"  {item['file']}: [[{item['link']}]] — {item['reason']}")

    if args.strict:
        sys.exit(1 if broken else 0)
    else:
        print("\n(빌드는 계속 진행됩니다)")
        sys.exit(0)


if __name__ == "__main__":
    main()