"""Tier-0 알림 큐 — 콘텐츠 수준의 시한성 정보를 로컬 파일에 적재한다.

기존 텔레그램 알림은 *파이프라인 실행 결과*(몇 건 처리했는지)만 보낸다. 이 모듈은
*콘텐츠 안에 들어 있는* 시한성 사실 — API 키 만료, 보안 취약점, 마감일, 진행 중
프로젝트에 영향을 주는 정책 변화 — 을 따로 모은다.

설계
----
- Tier 0 (항상): `alerts/notifications.md` 에 append + stdout 출력. 외부 의존 없음.
- Tier 1 (선택): 파이프라인의 기존 텔레그램 알림에 미해결 건수를 실어 보낸다.
- 보고한 항목은 `--clear` 로 `alerts/archive/` 로 옮긴다. 큐가 무한히 자라지 않게.

사용법
------
    python3 scripts/alerts.py add "OpenAI API 키 2026-09-01 만료" --category account_expiry
    python3 scripts/alerts.py list                # 미해결 목록
    python3 scripts/alerts.py list --json
    python3 scripts/alerts.py clear               # 전체 아카이브
    python3 scripts/alerts.py clear --id 3        # 특정 항목만
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ALERTS_DIR = PROJECT_DIR / "alerts"
QUEUE_FILE = ALERTS_DIR / "notifications.md"
ARCHIVE_DIR = ALERTS_DIR / "archive"

CATEGORIES = {
    "security": "🔒 보안",
    "account_expiry": "⏳ 만료",
    "project_risk": "⚠️ 프로젝트 리스크",
    "deadline": "📅 마감",
    "interest": "💡 관심",
}

QUEUE_HEADER = """---
title: 알림 큐
type: alert-queue
updated: {updated}
---

# 알림 큐 (Tier 0)

콘텐츠에서 발견된 시한성 정보. 보고 후 `python3 scripts/alerts.py clear` 로 아카이브한다.

"""

ENTRY_RE = re.compile(
    r"^- \[(?P<state>[ x])\] \*\*(?P<cat>[^*]+)\*\* (?P<msg>.*?)"
    r"(?:\s+·\s+출처: (?P<source>\S+))?"
    r"\s+·\s+등록: (?P<added>\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)


def _ensure_queue() -> None:
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        QUEUE_FILE.write_text(
            QUEUE_HEADER.format(updated=datetime.now().date().isoformat()),
            encoding="utf-8",
        )


def _read_entries() -> list[dict]:
    if not QUEUE_FILE.exists():
        return []
    text = QUEUE_FILE.read_text(encoding="utf-8", errors="replace")
    out = []
    for i, m in enumerate(ENTRY_RE.finditer(text), start=1):
        out.append({
            "id": i,
            "done": m.group("state") == "x",
            "category": m.group("cat").strip(),
            "message": m.group("msg").strip(),
            "source": m.group("source") or "",
            "added": m.group("added"),
            "raw": m.group(0),
        })
    return out


def add(message: str, category: str = "interest", source: str = "") -> dict:
    """알림을 큐에 추가한다. 같은 메시지가 이미 미해결이면 중복 등록하지 않는다."""
    _ensure_queue()
    if category not in CATEGORIES:
        print(f"알 수 없는 카테고리 {category!r} — interest 로 기록한다.", file=sys.stderr)
        category = "interest"

    label = CATEGORIES[category]
    for e in _read_entries():
        if not e["done"] and e["message"] == message.strip():
            print(f"이미 큐에 있음 (#{e['id']}): {message}")
            return e

    today = datetime.now().date().isoformat()
    line = f"- [ ] **{label}** {message.strip()}"
    if source:
        line += f" · 출처: {source}"
    line += f" · 등록: {today}"

    text = QUEUE_FILE.read_text(encoding="utf-8")
    text = re.sub(r"^updated: .*$", f"updated: {today}", text, count=1, flags=re.MULTILINE)
    if not text.endswith("\n"):
        text += "\n"
    QUEUE_FILE.write_text(text + line + "\n", encoding="utf-8")

    print(f"[알림] {label} {message}")
    return {"category": label, "message": message, "source": source, "added": today}


def pending() -> list[dict]:
    return [e for e in _read_entries() if not e["done"]]


def clear(entry_id: int | None = None) -> int:
    """미해결 항목을 아카이브로 옮긴다. 옮긴 건수를 반환."""
    entries = _read_entries()
    targets = [e for e in entries if not e["done"] and (entry_id is None or e["id"] == entry_id)]
    if not targets:
        print("아카이브할 항목이 없다.")
        return 0

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_file = ARCHIVE_DIR / f"{stamp}.md"
    archive_file.write_text(
        f"# 아카이브 {stamp}\n\n" + "\n".join(t["raw"] for t in targets) + "\n",
        encoding="utf-8",
    )

    text = QUEUE_FILE.read_text(encoding="utf-8")
    for t in targets:
        text = text.replace(t["raw"] + "\n", "")
        text = text.replace(t["raw"], "")
    QUEUE_FILE.write_text(text, encoding="utf-8")

    print(f"{len(targets)}건 아카이브: {archive_file.relative_to(PROJECT_DIR)}")
    return len(targets)


def format_for_notification(limit: int = 5) -> str:
    """파이프라인 텔레그램 알림에 붙일 요약. 미해결이 없으면 빈 문자열."""
    items = pending()
    if not items:
        return ""
    lines = [f"🔔 *미해결 알림 {len(items)}건*"]
    for e in items[:limit]:
        lines.append(f"  • {e['category']} {e['message']}")
    if len(items) > limit:
        lines.append(f"  _외 {len(items) - limit}건…_")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Tier-0 알림 큐")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="알림 추가")
    p_add.add_argument("message")
    p_add.add_argument("--category", default="interest", choices=sorted(CATEGORIES))
    p_add.add_argument("--source", default="", help="출처 클리핑 슬러그")

    p_list = sub.add_parser("list", help="미해결 알림 목록")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument("--all", action="store_true", help="아카이브 전 항목 포함")

    p_clear = sub.add_parser("clear", help="알림 아카이브")
    p_clear.add_argument("--id", type=int, default=None)

    args = ap.parse_args()

    if args.cmd == "add":
        add(args.message, args.category, args.source)
        return 0

    if args.cmd == "list":
        items = _read_entries() if args.all else pending()
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
        elif not items:
            print("미해결 알림 없음.")
        else:
            for e in items:
                mark = "x" if e["done"] else " "
                src = f"  ({e['source']})" if e["source"] else ""
                print(f"[{mark}] #{e['id']:<3} {e['category']}  {e['message']}{src}  · {e['added']}")
        return 0

    if args.cmd == "clear":
        clear(args.id)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
