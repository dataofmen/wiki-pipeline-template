"""Broken 위키링크 제거 도구 — 기본 dry-run.

주의: 이 볼트에서 broken bare-slug 링크 대부분(약 71%)은 **의도된 stub 링크**다.
아직 노트를 쓰지 않은 개념을 미리 링크해 두는 디지털 가든 방식이고, Quartz 는 이를
"아직 쓰지 않은 노트" 로 렌더링한다. 따라서 전체 일괄 제거는 거의 항상 오답이다.
개념명 stub 은 `create_stubs.py` 로 노트를 만들어 해소하는 쪽이 맞다.

이 스크립트는 **범위를 좁혀서** 쓴다. 예: 대상이 영구 소실된 날짜 슬러그만.

이력
----
`item['reason']` 이 실제로는 `"no matching file found"` 인데 필터가
`"not found" in reason` 을 보고 있어서 bare-slug broken link 를 **한 건도 잡지
못했다**(2026-08-27 발견). 필터를 고치면 8,750건을 한 번에 지울 수 있게 되므로,
같이 dry-run 기본값과 범위 옵션을 넣었다.

사용법
------
    python3 scripts/remove_broken_links.py                      # 전체 dry-run(요약)
    python3 scripts/remove_broken_links.py --category dated     # 날짜 슬러그만 미리보기
    python3 scripts/remove_broken_links.py --category dated --apply
    python3 scripts/remove_broken_links.py --only wiki/concepts --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_links  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent

# check_links 가 내는 실제 사유 문자열. 오타 하나로 필터가 죽었던 자리이므로 상수로 둔다.
REASON_BARE_MISSING = "no matching file found"
REASON_PATH_MISSING_PREFIX = "path-prefixed target not found"

DATED_RE = re.compile(r"^\d{8}-")
HANGUL_RE = re.compile(r"[가-힣]")


def categorize(link: str) -> str:
    """링크 타깃을 성격별로 분류한다. 범위를 좁혀 작업하기 위한 것."""
    base = link.split("|")[0].split("#")[0].strip()
    if DATED_RE.match(base.split("/")[-1]):
        return "dated"          # 클리핑·노트 슬러그 — 대상이 사라진 provenance 링크
    # 한글 개념명은 공백을 포함해도 정상적인 stub 타깃이다("메타 인지" 등).
    # 공백 검사를 먼저 하면 한글 개념 2,000건이 phrase 로 오분류된다.
    if HANGUL_RE.search(base):
        return "concept-ko"     # 한글 개념명 — stub 후보
    # 실측 결과 다어절 영문 타깃도 [[Ralph Loop]] [[KV cache]] [[Mitchell Hashimoto]]
    # 처럼 정상적인 개념·인물 stub 이었다. "잘못된 링크" 로 취급하면 안 된다.
    if " " in base:
        return "concept-en-multi"   # 다어절 영문 개념명 — stub 후보
    return "concept-en"             # 영문 개념명 — stub 후보


CATEGORIES = ("dated", "concept-ko", "concept-en", "concept-en-multi", "path")


def collect(root: Path) -> list[dict]:
    broken, _ = check_links.check_links(root)
    out = []
    for item in broken:
        reason = item["reason"]
        if reason == REASON_BARE_MISSING:
            cat = categorize(item["link"])
        elif reason.startswith(REASON_PATH_MISSING_PREFIX):
            cat = "path"
        else:
            continue
        out.append({**item, "category": cat})
    return out


def strip_links(items: list[dict], *, apply: bool = False) -> tuple[int, int]:
    """대상 링크를 제거한다. `[[a|b]]` → `b`, `[[a]]` → `a` (텍스트는 남긴다)."""
    files_to_fix: dict[str, list[str]] = defaultdict(list)
    for item in items:
        files_to_fix[item["file"]].append(item["link"])

    changed_files = 0
    changed_links = 0
    for rel_path, links in files_to_fix.items():
        file_path = PROJECT_DIR / rel_path
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8", errors="replace")
        new_content = content
        # 긴 것부터 치환해 부분 일치를 막는다
        for link in sorted(set(links), key=len, reverse=True):
            target = f"[[{link}]]"
            if target not in new_content:
                continue
            if "|" in link:
                replacement = link.split("|", 1)[1]
            else:
                base = link.split("#")[0]
                replacement = base.split("/")[-1] if "/" in base else base
            changed_links += new_content.count(target)
            new_content = new_content.replace(target, replacement)
        if new_content != content:
            changed_files += 1
            if apply:
                file_path.write_text(new_content, encoding="utf-8")
                print(f"  수정: {rel_path}")
            else:
                print(f"  [dry-run] {rel_path} — {len(set(links))}개 타깃")
    return changed_files, changed_links


def main() -> int:
    ap = argparse.ArgumentParser(description="broken 위키링크 제거 (기본 dry-run)")
    ap.add_argument("--category", choices=CATEGORIES, action="append", default=None,
                    help="이 성격의 링크만 대상으로 한다. 여러 번 지정 가능. "
                         "concept-ko/concept-en 은 stub 링크일 가능성이 높아 신중히.")
    ap.add_argument("--only", default="", help="경로 접두사로 범위 제한 (예: wiki/concepts)")
    ap.add_argument("--apply", action="store_true", help="실제로 파일을 수정한다")
    args = ap.parse_args()

    items = collect(PROJECT_DIR)
    total = len(items)
    by_cat = Counter(i["category"] for i in items)

    print(f"broken 링크 {total}건")
    for c in CATEGORIES:
        if by_cat[c]:
            uniq = len({i["link"] for i in items if i["category"] == c})
            note = "  ← stub 링크 가능성 높음" if c.startswith("concept") else ""
            print(f"  {c:<12} {by_cat[c]:>5}건 (고유 {uniq}개){note}")
    print()

    if not args.category and not args.only:
        print("범위를 지정하지 않았다. 전체 일괄 제거는 stub 링크 그래프를 파괴하므로")
        print("지원하지 않는다. --category 또는 --only 로 범위를 좁혀 실행할 것.")
        print("개념명 stub 은 create_stubs.py 로 노트를 만드는 쪽이 맞다.")
        return 0

    targets = items
    if args.category:
        targets = [i for i in targets if i["category"] in set(args.category)]
    if args.only:
        targets = [i for i in targets if i["file"].startswith(args.only)]

    if not targets:
        print("범위 내 대상 없음.")
        return 0

    print(f"대상 {len(targets)}건 (파일 {len({i['file'] for i in targets})}개)")
    files, links = strip_links(targets, apply=args.apply)
    print(f"\n{'수정' if args.apply else '수정 예정'}: 파일 {files}개 · 링크 {links}건")
    if not args.apply:
        print("실제 적용은 --apply 를 붙여 실행한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
