#!/usr/bin/env python3
"""Unified, idempotent normalization layer for the knowledge pipeline.

Consolidates the previously scattered fix_* scripts (fix_dates, fix_yaml_titles,
fix_truncated_titles, fix_clippings_titles, fix_paths) into one deterministic
pass. Running it twice in a row must produce zero changes on the second run.

Stages (in order):
  1. Title normalization  — strip markdown headers/links/images/URLs/hashtag
     dumps/metadata lines, unwrap truncated links, apply overnight/transcript
     templates, cap length. Frontmatter-only edit (body preserved via guard).
  2. Date consistency      — ensure `created` exists; frontmatter value wins,
     filename YYYYMMDD is a *fallback only* (never overrides frontmatter).
  3. Wikilink cleanup      — rewrite path-prefixed [[concepts/x]] -> [[x]].

All writes go through scripts/safe_write.py integrity guards.

Usage:
    python3 scripts/normalize.py [--root DIR ...] [--apply]
    (no --apply = dry run: report change counts only)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import safe_write, split_frontmatter, write_frontmatter_only  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = [
    PROJECT_DIR / "clippings",
    PROJECT_DIR / "wiki" / "concepts",
    PROJECT_DIR / "wiki" / "entities",
    PROJECT_DIR / "garden",
]
TITLE_CAP = 90

# ------------------------------------------------------------------ title rules


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        inner = inner.replace("''", "'") if v[0] == "'" else inner.replace('\\"', '"')
        return inner
    return v


def clean_inline(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^#{1,6}\s+", "", s)                      # leading header
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)            # images
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)        # closed links -> text
    s = re.sub(r"\[([^\]]+)\]\([^)]*$", r"\1", s)         # truncated link -> text
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = s.replace("*", "").strip()
    if s.startswith("[") and "]" not in s:
        s = s.lstrip("[").strip()
    s = re.sub(r"\\([_*\[\]()#`~>+-])", r"\1", s)         # unescape md escapes
    s = re.sub(r"\s+", " ", s).strip().strip("\"'").strip()
    s = s.rstrip(":：-—·").strip()
    return s


def first_body_line(body: str) -> str:
    for ln in (body or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s.startswith(">"):
            continue
        if re.match(r"^!\[", s):
            continue
        if re.match(r"^#{1,6}\s", s):
            s = re.sub(r"^#{1,6}\s+", "", s)
        if s.lower() in ("summary", "raw briefing", "오프닝"):
            continue
        if s.startswith("- ") or s.startswith("* "):
            s = s[2:].strip()
        if s.lower().startswith("**url"):
            continue
        if "http" in s and len(re.sub(r"https?://\S+", "", s).strip(" :·-—")) < 8:
            continue
        if len(s) < 4 or (re.match(r"^[A-Z]+$", s) and " " not in s):
            continue
        return s
    return ""


def _cap(s: str) -> str:
    if len(s) <= TITLE_CAP:
        return s
    cut = s[:TITLE_CAP]
    for sep in (". ", "? ", "! ", " "):
        idx = cut.rfind(sep)
        if idx > TITLE_CAP * 0.5:
            cut = cut[: idx + (0 if sep == " " else 1)]
            break
    return cut.rstrip() + "…"


def _is_broken(t: str) -> bool:
    if not t:
        return True
    if "Source:" in t and "Captured:" in t:
        return True
    if t.startswith("X 스레드"):
        return True
    if t.startswith("![") or "_page_" in t:
        return True
    if t.count("#") >= 2 and len(re.sub(r"#\S+", "", t).strip()) < 6:
        return True
    if t.startswith("[") and "]" not in t:
        return True
    if re.match(r"^\[[^\]]+\]\(", t) or (t.startswith("[") and "](" in t):
        return True
    if re.match(r"^https?://", t):
        return True
    return False


def _is_slug(t: str) -> bool:
    return bool(re.match(r"^[0-9a-z]+(-[0-9a-z]+)+$", t)) and " " not in t


def compute_title(name: str, current: str, body: str, source_url: str = "") -> str:
    """Return the normalized title for a note (idempotent)."""
    if "overnight-ai-research" in name.lower() or "Overnight AI Research" in current:
        dm = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", name)
        if dm:
            return f"Overnight AI Research ({dm.group(1)}-{dm.group(2)}-{dm.group(3)})"
    em = re.search(r"(ep\d+)-ko-transcript", name)
    if em:
        return f"유튜브 트랜스크립트 ({em.group(1)})"

    cleaned = clean_inline(current)
    if _is_broken(current) or _is_broken(cleaned) or cleaned == "" or _is_slug(cleaned):
        cand = clean_inline(first_body_line(body))
        new = cand or cleaned
    else:
        new = cleaned

    if re.match(r"^https?://", new):
        from urllib.parse import urlparse
        src = source_url or new
        segs = [s for s in urlparse(src).path.split("/") if s and s not in ("p", "posts", "status")]
        if segs:
            words = re.sub(r"[-_]+", " ", segs[-1]).strip()
            if len(words) >= 4 and not words.isdigit():
                new = words[0].upper() + words[1:]

    new = _cap(new)
    return new or current

# --------------------------------------------------------------- frontmatter io


def _fm_field(fm: str, key: str) -> tuple[str | None, int, int]:
    """Return (value, start_line_idx, end_line_idx) for a frontmatter scalar (folded ok)."""
    lines = fm.split("\n")
    for i, ln in enumerate(lines):
        m = re.match(rf"^{key}:\s*(.*)$", ln)
        if not m:
            continue
        val, end = m.group(1), i
        j = i + 1
        while j < len(lines) and re.match(r"^\s+\S", lines[j]) and not re.match(r"^\s*[a-z_]+:\s", lines[j]):
            val += " " + lines[j].strip()
            end = j
            j += 1
        return val, i, end
    return None, -1, -1


def _set_field(fm: str, key: str, value: str, *, insert_if_missing: bool = False) -> str:
    lines = fm.split("\n")
    val, si, ei = _fm_field(fm, key)
    safe = json.dumps(value, ensure_ascii=False)
    if si == -1:
        if insert_if_missing:
            lines.insert(0, f"{key}: {safe}")
        return "\n".join(lines)
    lines[si] = f"{key}: {safe}"
    if ei > si:
        del lines[si + 1 : ei + 1]
    return "\n".join(lines)

# -------------------------------------------------------------------- stages


def normalize_file_frontmatter(path: Path) -> bool:
    """Apply title + date normalization to one file's frontmatter. Returns True if changed."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    if fm is None:
        return False
    new_fm = fm

    # --- title ---
    cur_title_raw, _, _ = _fm_field(new_fm, "title")
    source_url = _unquote((_fm_field(new_fm, "source_url")[0] or ""))
    cur_title = _unquote(cur_title_raw) if cur_title_raw is not None else ""
    new_title = compute_title(path.stem, cur_title, body, source_url)
    if cur_title_raw is None:
        # only insert a title if we could derive a non-empty one from the body
        if new_title:
            new_fm = _set_field(new_fm, "title", new_title, insert_if_missing=True)
    elif new_title != cur_title:
        new_fm = _set_field(new_fm, "title", new_title)

    # --- date consistency: frontmatter wins; filename is fallback only ---
    created_val, _, _ = _fm_field(new_fm, "created")
    date_val, _, _ = _fm_field(new_fm, "date")
    if created_val is None:
        fallback = date_val
        if fallback is None:
            dm = re.match(r"(\d{4})(\d{2})(\d{2})-", path.name)
            if dm:
                fallback = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
        if fallback is not None:
            new_fm = _set_field(new_fm, "created", _unquote(fallback), insert_if_missing=True)

    if new_fm == fm:
        return False
    write_frontmatter_only(path, new_fm)  # body preserved + shrink guard
    return True


WIKILINK_PREFIX = re.compile(r"\[\[(?:concepts|entities|garden|clippings|wiki)/([^\]\|]+)")


def normalize_file_wikilinks(path: Path) -> bool:
    """Rewrite path-prefixed wikilinks [[concepts/x]] -> [[x]]. Returns True if changed."""
    text = path.read_text(encoding="utf-8", errors="replace")
    new = WIKILINK_PREFIX.sub(r"[[\1", text)
    if new == text:
        return False
    safe_write(path, new)
    return True


def normalize_roots(roots: list[Path], *, apply: bool = False) -> dict:
    counts = {"title_or_date": 0, "wikilinks": 0, "files": 0}
    for root in roots:
        if not root.exists():
            continue
        for f in sorted(root.glob("**/*.md")):
            if f.name == "index.md":
                continue
            counts["files"] += 1
            if apply:
                if normalize_file_frontmatter(f):
                    counts["title_or_date"] += 1
                if normalize_file_wikilinks(f):
                    counts["wikilinks"] += 1
            else:
                # dry run: compute without writing
                text = f.read_text(encoding="utf-8", errors="replace")
                fm, body = split_frontmatter(text)
                if fm is not None:
                    cur_raw, _, _ = _fm_field(fm, "title")
                    cur = _unquote(cur_raw) if cur_raw is not None else ""
                    src = _unquote((_fm_field(fm, "source_url")[0] or ""))
                    nt = compute_title(f.stem, cur, body, src)
                    if (cur_raw is None and nt) or (cur_raw is not None and nt != cur):
                        counts["title_or_date"] += 1
                if WIKILINK_PREFIX.search(text):
                    counts["wikilinks"] += 1
    return counts


def generate_mocs() -> int:
    try:
        sys.path.insert(0, str(PROJECT_DIR / "scripts"))
        from generate_moc import generate_moc  # type: ignore
        files = generate_moc(PROJECT_DIR)
        return len(files) if files else 0
    except Exception as e:  # pragma: no cover
        print(f"[normalize] MOC generation skipped: {e}")
        return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Unified idempotent note normalizer")
    ap.add_argument("--root", action="append", default=None, help="Root dir(s) to normalize")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    ap.add_argument("--no-moc", action="store_true", help="Skip MOC generation")
    args = ap.parse_args()
    roots = [Path(r) for r in args.root] if args.root else DEFAULT_ROOTS
    counts = normalize_roots(roots, apply=args.apply)
    if args.apply and not args.no_moc:
        counts["mocs"] = generate_mocs()
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[normalize] {mode}: {counts}")


if __name__ == "__main__":
    main()
