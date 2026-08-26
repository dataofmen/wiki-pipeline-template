"""링크만 걸려 있고 노트가 없는 개념에 stub 노트를 만든다 — 기본 dry-run.

범위를 반드시 좁힌다. check_links 가 보고하는 미해결 링크에는 세 종류가 섞여 있고,
그중 stub 을 만들어야 하는 것은 하나뿐이다.

  body   × stub        → 대상. 아직 쓰지 않은 개념·인물 (디지털 가든의 의도)
  body   × dead-source → 대상 아님. 삭제된 클리핑을 가리키는 날짜 슬러그.
                         stub 을 만들면 없던 문서를 창작하는 셈이다.
  frontmatter          → 대상 아님. Quartz 가 렌더링하지 않는 메타데이터
                         (`source_clipping` 은 pipeline_state 가 쓰는 필드)

범위 제한 없이 돌리면 5,000개가 넘는 껍데기 노트가 생긴다.

사용법:
    python3 scripts/create_stubs.py              # 미리보기
    python3 scripts/create_stubs.py --apply
    python3 scripts/create_stubs.py --min-refs 3 --apply   # 3곳 이상에서 링크된 것만
"""
import argparse
import sys
from collections import Counter
from pathlib import Path
from datetime import datetime

# Import check_links logic
import check_links

def create_stubs(apply: bool = False, min_refs: int = 1):
    root = Path(__file__).parent.parent
    broken, _ = check_links.check_links(root)

    # 본문에 있고 성격이 stub 인 것만. dead-source·frontmatter 는 제외한다.
    candidates = [item['link'] for item in broken
                  if item['reason'] == "no matching file found"
                  and item.get('scope') == 'body'
                  and item.get('kind') == 'stub']
    refs = Counter(candidates)
    missing_links = [l for l, n in refs.items() if n >= min_refs]
    print(f"본문 stub 링크 {len(candidates)}건 / 고유 {len(refs)}개 · "
          f"{min_refs}회 이상 참조: {len(missing_links)}개")
    
    unique_slugs = set()
    for link in missing_links:
        clean = link.replace("\\", "/")
        if clean.endswith(".md"):
            clean = clean[:-3]
        if "/" in clean:
            clean = clean.split("/")[-1]
        
        unique_slugs.add(clean)

    if not unique_slugs:
        print("No missing links found.")
        return

    print(f"Found {len(unique_slugs)} unique missing links. Creating stubs...")

    today = datetime.now().strftime("%Y-%m-%d")
    concepts_dir = root / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    created_count = 0
    for slug in unique_slugs:
        if slug.startswith("-"):
            continue # ignore strange empty links like [[-]]
        
        stub_path = concepts_dir / f"{slug}.md"

        if not stub_path.exists():
            if not apply:
                print(f"  [dry-run] {stub_path.relative_to(root)}  ({refs[slug]}곳에서 참조)")
                created_count += 1
                continue
            content = f"""---
title: "{slug.replace('-', ' ').title()}"
target: machine
domain: unknown
cognitive_type: concept
type: concept
created: {today}
updated: {today}
status: stub
---
# {slug.replace('-', ' ').title()}

이 문서는 파이프라인에 의해 자동 생성된 빈 껍데기 파일(Stub)입니다. AI가 문맥 연결을 위해 링크를 걸었으나 아직 본문이 작성되지 않은 상태입니다. 나중에 내용을 채워주세요.
"""
            stub_path.write_text(content, encoding="utf-8")
            created_count += 1
            print(f"  생성: {stub_path.relative_to(root)}  ({refs[slug]}곳에서 참조)")

    verb = "생성" if apply else "생성 예정"
    print(f"\n{verb}: {created_count}개 (wiki/concepts/)")
    if not apply and created_count:
        print("실제 적용은 --apply 를 붙여 실행한다.")


def main() -> int:
    ap = argparse.ArgumentParser(description="개념 stub 노트 생성 (기본 dry-run)")
    ap.add_argument("--apply", action="store_true", help="실제로 파일을 만든다")
    ap.add_argument("--min-refs", type=int, default=1,
                    help="이 횟수 이상 참조된 개념만 (기본 1). 노이즈를 줄이려면 3 이상 권장")
    args = ap.parse_args()
    create_stubs(apply=args.apply, min_refs=args.min_refs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
