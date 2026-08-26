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
# `_quarantine` 은 scripts/sync_content.sh 의 rsync 에서 제외되므로 배포 사이트에
# 존재하지 않는다. 링크 검사도 같은 기준을 써야 "격리했는데 링크는 살아 있다"는
# 착시가 생기지 않는다.
EXCLUDE_DIRS = {"_workspace", "_workspace_prev", "inbox", "raw", ".claude", ".obsidian", ".omc",
                "_quarantine"}


# 소스 디렉토리만 인덱싱 (quartz/content/ 등 복사본 제외)
SOURCE_DIRS = ["wiki", "garden", "clippings"]


FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
DATED_RE = re.compile(r"^\d{8}-")
HANGUL_RE = re.compile(r"[가-힣]")

# 프론트매터 안의 위키링크는 **메타데이터**다. Quartz 는 프론트매터를 페이지 링크로
# 렌더링하지 않으므로 "사이트에서 깨진 링크" 가 아니다. 특히 `source_clipping` 은
# pipeline_state 가 "처리 완료" 판정에 쓰는 필드라 broken 으로 보고 지우면 클리핑
# 전체가 재생성된다. 반드시 본문 링크와 구분해서 센다.
#   실측(2026-08-27): 8,742건 중 3,639건이 프론트매터 전용
#   (target: 2,316 / source_clipping: 1,214)


def split_frontmatter_body(text: str) -> tuple[str, str]:
    m = FRONTMATTER_RE.match(text)
    return (m.group(0), text[len(m.group(0)):]) if m else ("", text)


def classify_target(link: str) -> str:
    """broken 타깃의 성격. 의도된 stub 과 실제 소실을 구분한다."""
    base = link.split("|")[0].split("#")[0].strip()
    leaf = base.split("/")[-1]
    if DATED_RE.match(leaf):
        return "dead-source"    # 날짜 슬러그 — 대상 클리핑/노트가 사라짐
    if HANGUL_RE.search(base) or " " in base or base.islower() or base[:1].isupper():
        return "stub"           # 개념·인물명 — 아직 쓰지 않은 노트(디지털 가든의 의도)
    return "stub"

def _iter_markdown(src_dir: Path):
    """EXCLUDE_DIRS 를 실제로 걸러 마크다운을 순회한다.

    (기존에는 EXCLUDE_DIRS 가 선언만 되고 rglob 에 적용되지 않아, 배포에서 빠지는
     `_quarantine` 노트가 링크를 만족시키는 것으로 잡혔다 — 격리 후에도 broken link
     가 0으로 보이는 착시의 원인.)
    """
    for md in src_dir.rglob("*.md"):
        if EXCLUDE_DIRS & set(md.parts):
            continue
        yield md


def build_slug_index(root: Path) -> dict[str, list[str]]:
    """파일명(stem) → 전체 상대경로 목록 매핑. 동일 파일명이 여러 디렉토리에 있으면 ambiguous."""
    index: dict[str, list[str]] = defaultdict(list)
    for src in SOURCE_DIRS:
        src_dir = root / src
        if not src_dir.exists():
            continue
        for md in _iter_markdown(src_dir):
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
        for md in _iter_markdown(src_dir):
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
        for md in _iter_markdown(d):
            content = md.read_text(encoding="utf-8", errors="replace")
            head, _body = split_frontmatter_body(content)
            head_end = len(head)
            for match in WIKILINK_RE.finditer(content):
                target = match.group(1).strip()
                # 프론트매터 안이면 메타데이터 참조로 표시한다
                scope = "frontmatter" if match.start() < head_end else "body"
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
                            "scope": scope,
                            "kind": classify_target(target),
                        })
                else:
                    # bare slug: shortest 해상도 시뮬레이션
                    matches = slug_index.get(target_clean, [])
                    if len(matches) == 0:
                        broken.append({
                            "file": str(md.relative_to(root)),
                            "link": target,
                            "reason": "no matching file found",
                            "scope": scope,
                            "kind": classify_target(target),
                        })
                    elif len(matches) > 1:
                        ambiguous.append({
                            "file": str(md.relative_to(root)),
                            "link": target,
                            "reason": f"ambiguous: {len(matches)} files match: {matches}",
                            "scope": scope,
                            "kind": classify_target(target),
                        })
                    # len(matches) == 1: 정상 (unique slug)

    return broken, ambiguous


def summarize(broken: list[dict], ambiguous: list[dict]) -> dict:
    """건강 지표. `actionable` 만이 회귀 판정 대상이다.

    나머지는 성격상 0 이 될 수 없는 항목이다.
      - stub          : 아직 쓰지 않은 노트를 미리 링크하는 디지털 가든 방식. 의도된 것.
      - frontmatter   : Quartz 가 링크로 렌더링하지 않는 메타데이터
                        (`source_clipping` 은 pipeline_state 가 쓰는 필드다).
    이 둘을 합쳐 한 숫자로 보고하면 회귀가 노이즈에 묻힌다 — 실제로 8,742 라는 수치
    안에서 새로 생긴 깨진 링크를 아무도 발견할 수 없었다.
    """
    def n(items, **kw):
        return len([x for x in items
                    if all(x.get(k) == v for k, v in kw.items())])
    return {
        "actionable": n(broken, scope="body", kind="dead-source"),
        "body_stub": n(broken, scope="body", kind="stub"),
        "frontmatter": n(broken, scope="frontmatter"),
        "ambiguous_body": n(ambiguous, scope="body"),
        "broken_total": len(broken),
        "ambiguous_total": len(ambiguous),
    }


BASELINE_FILE = WIKI_ROOT / "reports" / "link-health-baseline.json"


def load_baseline() -> dict | None:
    import json
    try:
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))["metrics"]
    except Exception:
        return None


def save_baseline(metrics: dict, note: str = "") -> None:
    import json
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps({"note": note, "metrics": metrics}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(
        description="위키링크 건강 검사 (Quartz shortest 해상도 시뮬레이션)")
    parser.add_argument("--root", default=str(WIKI_ROOT), help="wiki 루트 디렉토리")
    parser.add_argument("--strict", action="store_true",
                        help="actionable(본문·대상소실) 이 있거나 기준선보다 악화되면 exit 1")
    parser.add_argument("--list", action="store_true", help="actionable 항목을 나열")
    parser.add_argument("--update-baseline", action="store_true",
                        help="현재 수치를 기준선으로 기록")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    broken, ambiguous = check_links(root)
    m = summarize(broken, ambiguous)

    if args.json:
        import json
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print("링크 건강")
        print(f"  ▸ 조치 필요 (본문·대상 소실) : {m['actionable']}건   ← 0 이어야 한다")
        print(f"    의도된 stub (본문)         : {m['body_stub']}건")
        print(f"    메타데이터 (프론트매터)      : {m['frontmatter']}건")
        print(f"    중복 슬러그 (본문, ambiguous): {m['ambiguous_body']}건")

    base = load_baseline()
    regressed = False
    if base:
        print("\n기준선 대비")
        for k in ("actionable", "body_stub", "ambiguous_body"):
            d = m[k] - base.get(k, 0)
            mark = "→" if d == 0 else ("↑ 악화" if d > 0 else "↓ 개선")
            print(f"  {k:<16} {base.get(k, 0):>6} → {m[k]:>6}  {mark}{f' ({d:+d})' if d else ''}")
            if k == "actionable" and d > 0:
                regressed = True
    else:
        print("\n기준선 없음 — --update-baseline 으로 기록할 수 있다.")

    if args.list and m["actionable"]:
        print("\n조치 필요 항목:")
        for x in broken:
            if x.get("scope") == "body" and x.get("kind") == "dead-source":
                print(f"  {x['file']}: [[{x['link']}]]")

    if args.update_baseline:
        save_baseline(m, note="scripts/check_links.py --update-baseline 로 기록")
        print(f"\n기준선 기록: {BASELINE_FILE.relative_to(WIKI_ROOT)}")

    if args.strict and (m["actionable"] > 0 or regressed):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()