#!/usr/bin/env python3
"""Read-only diagnosis of the knowledge pipeline state.

Prints a summary of each stage and writes tasks/diagnosis-report.md.
Never modifies clippings/, wiki/, or garden/ — inspection only.

Usage:
    python3 scripts/diagnose_pipeline.py
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
INBOX_DIR = PROJECT_DIR / "inbox"
CLIPPINGS_DIR = PROJECT_DIR / "clippings"
WIKI_DIR = PROJECT_DIR / "wiki"
GARDEN_DIR = PROJECT_DIR / "garden"
PIPELINE_LOG = PROJECT_DIR / "scripts" / "pipeline.log"
REPORT_PATH = PROJECT_DIR / "tasks" / "diagnosis-report.md"

# Log lines matching these patterns are counted as failures, keyed by a short label.
ERROR_PATTERNS = [
    ("codex exec exited (note-gen dead)", re.compile(r"codex exec exited \d+")),
    ("No new notes created", re.compile(r"No new notes created")),
    ("codex exec timed out", re.compile(r"codex exec timed out")),
    ("phase_publish crashed", re.compile(r"phase_publish crashed")),
    ("Content audit failed", re.compile(r"Content audit failed")),
    ("Traceback", re.compile(r"Traceback \(most recent call last\)")),
    ("generic ERROR", re.compile(r"\[ERROR\]")),
]


def _count_md(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.glob("**/*.md"))


def _inbox_pending() -> int:
    """Count inbox items not yet marked processed.

    The pipeline marks processed items; here we conservatively count markdown /
    text payloads under inbox/ subfolders, excluding logs and *.processed markers.
    """
    if not INBOX_DIR.exists():
        return 0
    count = 0
    for f in INBOX_DIR.glob("**/*"):
        if not f.is_file():
            continue
        if f.suffix in {".log"} or f.name.endswith(".processed"):
            continue
        if f.suffix in {".md", ".txt", ".pdf"} and ".meta." not in f.name:
            count += 1
    return count


def _clippings_referenced_by_notes() -> set[str]:
    """Return set of clipping stems that at least one note points to via source_clipping."""
    refs: set[str] = set()
    pat = re.compile(r'source_clipping:\s*["\']?\s*\[\[([^\]\n|]+)(?:\|[^\]]*)?\]\]')
    for directory in (WIKI_DIR, GARDEN_DIR):
        if not directory.exists():
            continue
        for f in directory.glob("**/*.md"):
            try:
                m = pat.search(f.read_text(errors="ignore"))
            except Exception:
                continue
            if m:
                refs.add(re.sub(r"\.md$", "", m.group(1).strip()))
    return refs


def _unprocessed_clippings() -> list[str]:
    refs = _clippings_referenced_by_notes()
    out = []
    if CLIPPINGS_DIR.exists():
        for f in sorted(CLIPPINGS_DIR.glob("*.md")):
            if f.stem not in refs:
                out.append(f.name)
    return out


def _undeployed_changes() -> list[str]:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "wiki/", "garden/", "clippings/"],
            cwd=PROJECT_DIR, capture_output=True, text=True, errors="replace",
        )
    except Exception as e:  # pragma: no cover - git always present here
        return [f"(git status failed: {e})"]
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _log_error_summary(max_lines: int = 4000) -> list[tuple[str, int]]:
    if not PIPELINE_LOG.exists():
        return []
    try:
        lines = PIPELINE_LOG.read_text(errors="ignore").splitlines()[-max_lines:]
    except Exception:
        return []
    counts: Counter[str] = Counter()
    for label, pat in ERROR_PATTERNS:
        n = sum(1 for ln in lines if pat.search(ln))
        if n:
            counts[label] = n
    return counts.most_common(3)


def build_report() -> str:
    inbox_pending = _inbox_pending()
    clip_total = len(list(CLIPPINGS_DIR.glob("*.md"))) if CLIPPINGS_DIR.exists() else 0
    concepts = _count_md(WIKI_DIR / "concepts")
    entities = _count_md(WIKI_DIR / "entities")
    garden = _count_md(GARDEN_DIR)
    unprocessed = _unprocessed_clippings()
    undeployed = _undeployed_changes()
    top_errors = _log_error_summary()

    lines: list[str] = []
    lines.append("# 지식 파이프라인 진단 리포트")
    lines.append("")
    lines.append(f"> 생성: {datetime.now().isoformat(timespec='seconds')} · read-only 진단 (scripts/diagnose_pipeline.py)")
    lines.append("")
    lines.append("## 단계별 상태")
    lines.append("")
    lines.append("| 지표 | 값 |")
    lines.append("|------|----|")
    lines.append(f"| inbox 대기 항목 | {inbox_pending} |")
    lines.append(f"| clippings 총계 | {clip_total} |")
    lines.append(f"| 노트로 처리된 clipping | {clip_total - len(unprocessed)} |")
    lines.append(f"| 미처리 clipping (노트 없음) | {len(unprocessed)} |")
    lines.append(f"| wiki/concepts 노트 | {concepts} |")
    lines.append(f"| wiki/entities 노트 | {entities} |")
    lines.append(f"| garden 노트 | {garden} |")
    lines.append(f"| 미배포 git 변경 (wiki/garden/clippings) | {len(undeployed)} |")
    lines.append("")

    lines.append("## 최근 로그 상위 실패 원인 (top 3)")
    lines.append("")
    if top_errors:
        for label, n in top_errors:
            lines.append(f"- **{label}**: {n}건")
    else:
        lines.append("- (pipeline.log 없음 또는 오류 패턴 없음)")
    lines.append("")

    if unprocessed:
        lines.append("## 미처리 clipping 목록 (최대 30건)")
        lines.append("")
        for name in unprocessed[:30]:
            lines.append(f"- {name}")
        if len(unprocessed) > 30:
            lines.append(f"- … 외 {len(unprocessed) - 30}건")
        lines.append("")

    if undeployed:
        lines.append("## 미배포 변경 (최대 20건)")
        lines.append("")
        for ln in undeployed[:20]:
            lines.append(f"- `{ln}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    report = build_report()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    # Also echo the summary to stdout for immediate inspection.
    print(report)
    print(f"[diagnose] report written to {REPORT_PATH.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
