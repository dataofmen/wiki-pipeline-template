#!/usr/bin/env python3
"""Single source of truth for pipeline processing state + run locking.

A clipping is considered "processed" iff at least one note on disk references it
via `source_clipping: "[[<stem>]]"`. This replaces the previous, fragile mix of
JSONL done-files + disk scans: notes are the ground truth, so deleting/quarantining
a note automatically makes its clipping eligible for regeneration (idempotent).
"""
from __future__ import annotations

import fcntl
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

SOURCE_CLIPPING_RE = re.compile(
    r'source_clipping:\s*["\']?\s*\[\[([^\]\n|]+)(?:\|[^\]]*)?\]\]'
)


def processed_clipping_stems(roots: list[Path]) -> set[str]:
    """Return the set of clipping stems that at least one note references."""
    stems: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for f in root.glob("**/*.md"):
            try:
                m = SOURCE_CLIPPING_RE.search(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            if m:
                stems.add(re.sub(r"\.md$", "", m.group(1).strip()))
    return stems


def is_processed(clipping_name: str, roots: list[Path]) -> bool:
    stem = re.sub(r"\.md$", "", Path(clipping_name).name)
    return stem in processed_clipping_stems(roots)


def try_lock(lock_path: Path):
    """Acquire an exclusive non-blocking flock. Return the open fd on success,
    or None if another holder already owns it. Keep the returned fd alive for
    the duration of the critical section (closing it releases the lock)."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "a")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        fd.close()
        return None
