#!/usr/bin/env python3
"""Note frontmatter schema validation + fail-fast stage gates.

`validate_note` reports errors (critical) and warnings (non-blocking):
  errors   — no frontmatter / empty title  => note is quarantined, excluded from publish
  warnings — missing source_clipping / status / date => logged only

Quarantine is intentionally conservative: only structurally broken notes are
moved to `_quarantine/`, so the large existing vault is never mass-quarantined
for merely lacking optional provenance fields.

`stage_gate(name, ok)` logs a pass/fail marker for pipeline stage boundaries and
returns ok, so callers can `if not stage_gate(...): abort`.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_write import split_frontmatter  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("validate_schema")

REQUIRED_WARN_FIELDS = ("source_clipping", "status")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(PROJECT_DIR))
    except ValueError:
        return str(p)


def _field(fm: str, key: str) -> str | None:
    m = re.search(rf"^{key}:\s*(.*)$", fm, re.M)
    return m.group(1).strip().strip("\"'") if m else None


def validate_note(path: Path) -> dict:
    """Return {'errors': [...], 'warnings': [...]} for one note."""
    errors: list[str] = []
    warnings: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, _ = split_frontmatter(text)
    if fm is None:
        errors.append("no frontmatter block")
        return {"errors": errors, "warnings": warnings}

    title = _field(fm, "title")
    if not title:
        errors.append("missing/empty title")

    for key in REQUIRED_WARN_FIELDS:
        if _field(fm, key) is None:
            warnings.append(f"missing {key}")
    if _field(fm, "created") is None and _field(fm, "date") is None:
        warnings.append("missing date/created")
    return {"errors": errors, "warnings": warnings}


def quarantine(path: Path, root: Path) -> Path:
    """Move a broken note into <root>/_quarantine/ preserving its name."""
    qdir = root / "_quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    dest = qdir / path.name
    path.replace(dest)
    return dest


def validate_and_quarantine(dirs: list[Path], *, apply: bool = False) -> dict:
    """Validate notes in dirs. Quarantine error-notes (only when apply=True).

    Returns {'valid': n, 'quarantined': [...], 'warned': [...]}.
    """
    result = {"valid": 0, "quarantined": [], "warned": []}
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("**/*.md")):
            if f.name == "index.md" or "_quarantine" in f.parts:
                continue
            v = validate_note(f)
            if v["errors"]:
                if apply:
                    quarantine(f, d)
                result["quarantined"].append((_rel(f), v["errors"]))
            else:
                result["valid"] += 1
                if v["warnings"]:
                    result["warned"].append((_rel(f), v["warnings"]))
    return result


def stage_gate(name: str, ok: bool, detail: str = "") -> bool:
    """Log a stage-boundary gate result. Returns ok for use in `if not stage_gate(...)`."""
    marker = "PASS" if ok else "FAIL"
    msg = f"[gate:{name}] {marker}" + (f" — {detail}" if detail else "")
    (logger.info if ok else logger.error)(msg)
    return ok


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Validate note frontmatter schema")
    ap.add_argument("--apply", action="store_true", help="Quarantine broken notes")
    ap.add_argument("--root", action="append", default=None)
    args = ap.parse_args()
    roots = [Path(r) for r in args.root] if args.root else [PROJECT_DIR / "wiki", PROJECT_DIR / "garden"]
    r = validate_and_quarantine(roots, apply=args.apply)
    stage_gate("schema-validation", not r["quarantined"],
               f"valid={r['valid']} quarantined={len(r['quarantined'])} warned={len(r['warned'])}")
    for path, errs in r["quarantined"][:20]:
        print(f"  QUARANTINE {path}: {errs}")


if __name__ == "__main__":
    main()
