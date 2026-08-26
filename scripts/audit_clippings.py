"""클리핑 전수 검증 + 파생 노트 추적 (소급 정리 도구).

VERIFY 게이트는 앞으로 들어오는 것만 막는다. 이미 저장된 클리핑과, 그중 오염된
것에서 파생된 노트를 찾아내려면 별도의 전수 조사가 필요하다.

파생 노트는 노트 프론트매터의 `source_clipping: "[[슬러그]]"` 로 역추적한다
(파이프라인이 이 필드를 단일 진실 원천으로 쓰고 있으므로 신뢰할 수 있다).

사용법
------
    python3 scripts/audit_clippings.py                 # 요약 리포트
    python3 scripts/audit_clippings.py --verdict failed --show-notes
    python3 scripts/audit_clippings.py --json > reports/clipping-audit.json
    python3 scripts/audit_clippings.py --fix-status     # 실패 클리핑 프론트매터에 표시
    python3 scripts/audit_clippings.py --quarantine     # 파생 노트를 draft 로 강등
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_validation import validate_clipping_file  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
CLIPPINGS_DIR = PROJECT_DIR / "clippings"
NOTE_DIRS = [PROJECT_DIR / "wiki", PROJECT_DIR / "garden"]

# 클리핑 인덱스 등 클리핑이 아닌 파일.
EXCLUDE_NAMES = {"index.md"}

SOURCE_CLIPPING_RE = re.compile(
    r"^source_clipping:\s*[\"']?\[\[([^\]|]+?)(?:\|[^\]]*)?\]\][\"']?\s*$",
    re.MULTILINE,
)
STATUS_RE = re.compile(r"^status:\s*[\"']?([\w-]+)[\"']?\s*$", re.MULTILINE)


def build_note_index() -> dict[str, list[Path]]:
    """클리핑 슬러그 → 그 클리핑에서 파생된 노트 경로 목록."""
    index: dict[str, list[Path]] = defaultdict(list)
    for root in NOTE_DIRS:
        if not root.exists():
            continue
        for note in root.rglob("*.md"):
            if "_quarantine" in note.parts:
                continue
            try:
                head = note.read_text(encoding="utf-8", errors="replace")[:2500]
            except Exception:
                continue
            for m in SOURCE_CLIPPING_RE.finditer(head):
                stem = re.sub(r"\.md$", "", m.group(1).strip())
                index[stem].append(note)
    return index


def clipping_files() -> list[Path]:
    if not CLIPPINGS_DIR.exists():
        return []
    return sorted(p for p in CLIPPINGS_DIR.glob("*.md") if p.name not in EXCLUDE_NAMES)


def audit() -> list[dict]:
    note_index = build_note_index()
    rows = []
    for path in clipping_files():
        v = validate_clipping_file(path)
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:1500]
        except Exception:
            head = ""
        m = STATUS_RE.search(head)
        rows.append({
            "clipping": str(path.relative_to(PROJECT_DIR)),
            "stem": path.stem,
            "verdict": v.verdict,
            "code": v.code,
            "reason": v.reason,
            "word_count": v.word_count,
            "frontmatter_status": m.group(1) if m else "",
            "derived_notes": [str(n.relative_to(PROJECT_DIR)) for n in note_index.get(path.stem, [])],
        })
    return rows


def fix_status(rows: list[dict], dry_run: bool = True) -> int:
    """FAILED 클리핑의 프론트매터를 `status: fetch_failed` + content_warning 으로 갱신."""
    changed = 0
    for r in rows:
        if r["verdict"] != "failed":
            continue
        if r["frontmatter_status"] == "fetch_failed":
            continue
        path = PROJECT_DIR / r["clipping"]
        text = path.read_text(encoding="utf-8", errors="replace")
        new = STATUS_RE.sub("status: fetch_failed", text, count=1)
        if "content_warning:" not in new:
            new = new.replace(
                "status: fetch_failed",
                f"status: fetch_failed\ncontent_warning: {r['code']}\n"
                f"content_warning_reason: \"{r['reason']}\"",
                1,
            )
        if new == text:
            continue
        changed += 1
        if dry_run:
            print(f"  [dry-run] {r['clipping']} → status: fetch_failed ({r['code']})")
        else:
            path.write_text(new, encoding="utf-8")
            print(f"  수정: {r['clipping']} → status: fetch_failed ({r['code']})")
    return changed


def quarantine_notes(rows: list[dict], dry_run: bool = True) -> int:
    """FAILED 클리핑에서 파생된 노트를 `_quarantine/` 로 옮긴다.

    삭제하지 않는다 — 사람이 확인하고 지우거나, 원문을 재추출해 다시 생성할 수 있게
    남긴다. `_quarantine/` 는 scripts/sync_content.sh 가 rsync 에서 제외하므로
    사이트에서 사라지고, pipeline_state 도 이 경로를 무시하므로 해당 클리핑이
    재생성 대상으로 다시 열린다.
    """
    changed = 0
    for r in rows:
        if r["verdict"] != "failed" or not r["derived_notes"]:
            continue
        for rel in r["derived_notes"]:
            path = PROJECT_DIR / rel
            if not path.exists() or "_quarantine" in path.parts:
                continue
            # wiki/concepts/foo.md → wiki/_quarantine/concepts/foo.md
            top = Path(rel).parts[0]
            subpath = Path(*Path(rel).parts[1:])
            dest = PROJECT_DIR / top / "_quarantine" / subpath

            text = path.read_text(encoding="utf-8", errors="replace")
            if "quarantine_reason:" not in text:
                stamped = STATUS_RE.sub("status: draft", text, count=1)
                stamped = stamped.replace(
                    "status: draft",
                    "status: draft\n"
                    f"quarantine_reason: \"원본 클리핑 검증 실패({r['code']}): {r['reason']}\"\n"
                    f"quarantine_source: \"{r['clipping']}\"",
                    1,
                )
            else:
                stamped = text

            changed += 1
            if dry_run:
                print(f"  [dry-run] {rel} → {dest.relative_to(PROJECT_DIR)} (원인: {r['stem']})")
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(stamped, encoding="utf-8")
                path.unlink()
                print(f"  격리: {rel} → {dest.relative_to(PROJECT_DIR)} (원인: {r['stem']})")
    return changed


def report(rows: list[dict], verdict_filter: str = "", show_notes: bool = False) -> None:
    counts = Counter(r["verdict"] for r in rows)
    codes = Counter(r["code"] for r in rows if r["verdict"] != "ok")
    total_notes = sum(len(r["derived_notes"]) for r in rows)
    tainted = [r for r in rows if r["verdict"] == "failed" and r["derived_notes"]]
    tainted_notes = sum(len(r["derived_notes"]) for r in tainted)

    print(f"클리핑 {len(rows)}건 검증 — "
          f"ok {counts['ok']} · suspect {counts['suspect']} · failed {counts['failed']}")
    print(f"파생 노트 총 {total_notes}개 · 그중 오염 의심 {tainted_notes}개 "
          f"(실패 클리핑 {len(tainted)}건에서 파생)")
    if codes:
        print("사유별: " + ", ".join(f"{k}={v}" for k, v in codes.most_common()))
    print()

    for r in rows:
        if r["verdict"] == "ok":
            continue
        if verdict_filter and r["verdict"] != verdict_filter:
            continue
        flag = "‼️" if (r["verdict"] == "failed" and r["derived_notes"]) else "  "
        print(f"{flag} {r['verdict']:<8} {r['code']:<18} {r['word_count']:>5}w  "
              f"{Path(r['clipping']).name}")
        print(f"     사유: {r['reason']}")
        if r["frontmatter_status"]:
            print(f"     프론트매터 status: {r['frontmatter_status']}")
        if r["derived_notes"]:
            print(f"     파생 노트 {len(r['derived_notes'])}개:")
            if show_notes or r["verdict"] == "failed":
                for n in r["derived_notes"]:
                    print(f"       - {n}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="클리핑 전수 검증 + 파생 노트 추적")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--verdict", default="", choices=["", "ok", "suspect", "failed"],
                    help="특정 판정만 출력")
    ap.add_argument("--show-notes", action="store_true", help="suspect 의 파생 노트도 나열")
    ap.add_argument("--fix-status", action="store_true",
                    help="실패 클리핑 프론트매터를 fetch_failed 로 갱신")
    ap.add_argument("--quarantine", action="store_true",
                    help="실패 클리핑의 파생 노트를 status: draft 로 강등")
    ap.add_argument("--apply", action="store_true",
                    help="--fix-status/--quarantine 를 실제로 기록 (없으면 dry-run)")
    args = ap.parse_args()

    rows = audit()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    report(rows, args.verdict, args.show_notes)

    if args.fix_status:
        print("=== 클리핑 status 갱신 ===")
        n = fix_status(rows, dry_run=not args.apply)
        print(f"{n}건 {'예정' if not args.apply else '완료'}\n")

    if args.quarantine:
        print("=== 파생 노트 격리 ===")
        n = quarantine_notes(rows, dry_run=not args.apply)
        print(f"{n}건 {'예정' if not args.apply else '완료'}\n")

    if (args.fix_status or args.quarantine) and not args.apply:
        print("실제 적용은 --apply 를 붙여 실행한다.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
