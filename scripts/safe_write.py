#!/usr/bin/env python3
"""Content-integrity guards for the knowledge pipeline.

A past incident truncated ~22k lines across 245 clippings and overwrote titles
with metadata garbage. These helpers make destructive writes fail loudly:

- `safe_write(path, content)` refuses to overwrite an existing file if the new
  content shrinks past a threshold (default 50%). New files are always allowed.
- `write_frontmatter_only(path, new_frontmatter)` rewrites ONLY the YAML
  frontmatter and asserts the body is byte-identical.
- `assert_body_unchanged(old, new)` raises if the post-frontmatter body differs.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_MAX_SHRINK_RATIO = 0.5  # refuse writes that drop >50% of bytes or lines
MIN_LINES_FOR_LINE_GUARD = 10   # ignore line-shrink guard on tiny files


class IntegrityError(Exception):
    """Raised when a write would destroy or truncate existing content."""


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_inner, body).

    If the text starts with a `---\\n ... \\n---` block, frontmatter_inner is the
    text between the fences and body is everything after the closing fence.
    Otherwise returns (None, text).
    """
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end == -1:
        return None, text
    fm_inner = text[4:end]
    # body starts after the closing fence line ("\n---" + optional "\n")
    rest = text[end + 4:]
    if rest.startswith("\n"):
        rest = rest[1:]
    return fm_inner, rest


def assert_body_unchanged(old_text: str, new_text: str) -> None:
    """Raise IntegrityError if the body (content after frontmatter) differs."""
    _, old_body = split_frontmatter(old_text)
    _, new_body = split_frontmatter(new_text)
    if old_body != new_body:
        raise IntegrityError(
            "body changed during a frontmatter-only edit "
            f"(old {len(old_body)} bytes vs new {len(new_body)} bytes)"
        )


def safe_write(
    path: str | Path,
    new_content: str,
    *,
    max_shrink_ratio: float = DEFAULT_MAX_SHRINK_RATIO,
) -> None:
    """Write new_content to path, refusing destructive shrinkage of an existing file.

    - Non-existent path: always written (new file).
    - Existing path: if new content drops below (1 - max_shrink_ratio) of the old
      byte count, OR below that fraction of the old line count (for non-tiny
      files), raise IntegrityError instead of writing.
    """
    path = Path(path)
    if path.exists() and path.is_file():
        old = path.read_text(encoding="utf-8", errors="replace")
        old_bytes, new_bytes = len(old), len(new_content)
        floor = 1.0 - max_shrink_ratio
        if old_bytes > 0 and new_bytes < old_bytes * floor:
            raise IntegrityError(
                f"refusing to write {path}: byte count {new_bytes} < "
                f"{floor:.0%} of existing {old_bytes} (destructive shrink)"
            )
        old_lines, new_lines = old.count("\n"), new_content.count("\n")
        if old_lines >= MIN_LINES_FOR_LINE_GUARD and new_lines < old_lines * floor:
            raise IntegrityError(
                f"refusing to write {path}: line count {new_lines} < "
                f"{floor:.0%} of existing {old_lines} (destructive shrink)"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_content, encoding="utf-8")


def write_frontmatter_only(path: str | Path, new_frontmatter: str) -> None:
    """Rewrite ONLY the YAML frontmatter of an existing file; body stays identical.

    new_frontmatter is the inner text (without the `---` fences). Raises
    IntegrityError if the file has no frontmatter or if the body would change.
    """
    path = Path(path)
    old = path.read_text(encoding="utf-8", errors="replace")
    fm_inner, body = split_frontmatter(old)
    if fm_inner is None:
        raise IntegrityError(f"{path} has no frontmatter block to edit")
    new_frontmatter = new_frontmatter.strip("\n")
    new_text = f"---\n{new_frontmatter}\n---\n{body}"
    assert_body_unchanged(old, new_text)
    safe_write(path, new_text)
