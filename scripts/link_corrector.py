#!/usr/bin/env python3
"""
Link Corrector & Backlinks Indexer

- Automatically detects and corrects wiki links:
  - Strips path prefixes (e.g., [[wiki/concepts/foo]] -> [[foo]])
  - Fixes file extensions (e.g., [[foo.md]] -> [[foo]])
  - Normalizes target names (e.g., lowercase / slug matching)
  - Uses fuzzy matching (difflib) to repair minor spelling mistakes in bare slugs
- Generates a backlinks index and injects a "## 🔗 역링크 (Backlinks)" section at the bottom of each note.
"""

import os
import re
import sys
import difflib
from pathlib import Path
from collections import defaultdict

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#([^\]|]*?))?(?:\|([^\]]*?))?\]\]")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
PROJECT_ROOT = Path(__file__).parent.parent
SOURCE_DIRS = ["wiki", "garden"]


def split_frontmatter(content: str) -> tuple[str, str]:
    """Split off the leading YAML frontmatter block, if present.

    Returns (frontmatter, body). frontmatter is "" if none found.
    Frontmatter fields like `source_clipping: "[[stem]]"` must never be
    touched by wikilink correction: they reference clippings/ (not indexed
    here), and fuzzy-matching against wiki/garden slugs has previously
    corrupted them into pointing at unrelated notes with similar dates.
    """
    m = FRONTMATTER_RE.match(content)
    if not m:
        return "", content
    return m.group(0), content[m.end():]


def build_clipping_stems(root: Path) -> set[str]:
    """Return the exact (case-sensitive) stems of every file under clippings/,
    including _quarantine. Body links like `[[{stem}|클리핑]]` intentionally
    reference a clipping, not a wiki/garden note — clippings aren't in
    slug_index, so leaving fuzzy-match on for these has repointed them at
    coincidentally similar-named wiki/garden notes (e.g. same topic slug,
    different date prefix)."""
    clippings_dir = root / "clippings"
    if not clippings_dir.exists():
        return set()
    return {f.stem for f in clippings_dir.rglob("*.md")}


def build_slug_index(root: Path) -> dict[str, str]:
    """Build index of bare_slug -> relative_path_without_extension.

    Uses lowercase for case-insensitive lookup.
    """
    index = {}
    for src in SOURCE_DIRS:
        src_dir = root / src
        if not src_dir.exists():
            continue
        for md in src_dir.rglob("*.md"):
            rel = str(md.relative_to(root).with_suffix(""))
            stem = md.stem
            # Store lowercased slug mapping to actual stem
            index[stem.lower()] = stem
            
            # Also map date-stripped versions to the same stem
            m = re.match(r"^\d{8}-(.+)$", stem)
            if m:
                index[m.group(1).lower()] = stem
    return index


def get_closest_match(slug: str, slug_list: list[str]) -> str | None:
    """Find closest slug in slug_list using difflib if similarity is high."""
    slug_lower = slug.lower()
    matches = difflib.get_close_matches(slug_lower, slug_list, n=1, cutoff=0.8)
    if matches:
        return matches[0]
    return None


def correct_links_in_content(content: str, slug_index: dict[str, str], slug_list_lower: list[str], clipping_stems: set[str] | None = None) -> tuple[str, list[str]]:
    """Scan content for wikilinks and correct them.

    Returns (corrected_content, list_of_valid_linked_slugs)
    """
    linked_slugs = []
    clipping_stems = clipping_stems or set()

    def replace_link(match):
        target = match.group(1).strip()
        heading = match.group(2)
        alias = match.group(3)

        # Normalize path
        target_clean = target.replace("\\", "/")
        if target_clean.endswith(".md"):
            target_clean = target_clean[:-3]

        # Extract bare slug if path-prefixed
        if "/" in target_clean:
            # e.g. concepts/2026-foo -> 2026-foo
            bare_slug = target_clean.split("/")[-1].strip()
        else:
            bare_slug = target_clean

        # A link that already exactly names a clipping (e.g. the "📎 클리핑:"
        # backlink to source_clipping) is correct as-is — never fuzzy-match it
        # against wiki/garden slugs.
        if bare_slug in clipping_stems:
            return match.group(0)

        bare_slug_lower = bare_slug.lower()

        # Resolve target slug
        resolved_slug = None
        if bare_slug_lower in slug_index:
            resolved_slug = slug_index[bare_slug_lower]
        else:
            # Attempt fuzzy match
            match_candidate = get_closest_match(bare_slug_lower, slug_list_lower)
            if match_candidate:
                resolved_slug = slug_index[match_candidate]

        if resolved_slug:
            linked_slugs.append(resolved_slug)
            # Reconstruct link
            link_str = resolved_slug
            if heading:
                link_str += f"#{heading.strip()}"
            if alias:
                link_str += f"|{alias.strip()}"
            elif target.lower() != resolved_slug.lower():
                link_str += f"|{target.strip()}"
            return f"[[{link_str}]]"
        else:
            # Keep original link if unresolved
            return match.group(0)

    # We only scan body content, not frontmatter if possible
    # But since wikilinks are rarely in frontmatter, replacing globally is fine.
    corrected = WIKILINK_RE.sub(replace_link, content)
    return corrected, linked_slugs


def update_backlinks_in_content(content: str, backlinks: list[tuple[str, str]]) -> str:
    """Remove injected backlinks section as Quartz natively supports it."""
    # Find existing backlinks section
    section_pattern = re.compile(
        r"\n*## 🔗 역링크\b.*?(?=\n##|$)",
        re.DOTALL
    )
    old_pattern = re.compile(
        r"\n*## 🔗 역링크 \(Backlinks\)\n.*?(?=\n##|$)",
        re.DOTALL
    )

    # Strip them out entirely
    content = section_pattern.sub("", content)
    content = old_pattern.sub("", content)
    
    return content


def main():
    print("=== Wiki Link Corrector & Backlinks Indexer ===")
    
    slug_index = build_slug_index(PROJECT_ROOT)
    slug_list_lower = list(slug_index.keys())
    clipping_stems = build_clipping_stems(PROJECT_ROOT)
    
    # Store references of: target_slug -> list of (source_stem, source_rel_path)
    backlinks_map = defaultdict(list)
    
    # Keep track of file paths and contents to update
    file_contents = {}
    file_links = {}

    # Step 1: Correct links and collect link references
    for src in SOURCE_DIRS:
        src_dir = PROJECT_ROOT / src
        if not src_dir.exists():
            continue
        for md in src_dir.rglob("*.md"):
            try:
                content = md.read_text(encoding="utf-8", errors="replace")
                frontmatter, body = split_frontmatter(content)
                corrected_body, linked_slugs = correct_links_in_content(body, slug_index, slug_list_lower, clipping_stems)
                corrected = frontmatter + corrected_body

                rel_path = str(md.relative_to(PROJECT_ROOT).with_suffix(""))
                stem = md.stem

                file_contents[md] = corrected
                file_links[md] = (stem, rel_path)

                # Record backlinks
                for target_slug in linked_slugs:
                    # Avoid self-backlink
                    if target_slug.lower() != stem.lower():
                        backlinks_map[target_slug.lower()].append((stem, rel_path))
            except Exception as e:
                print(f"Error reading {md.name}: {e}", file=sys.stderr)

    # Step 2: Inject backlinks and save files
    updated_count = 0
    for md, content in file_contents.items():
        stem, rel_path = file_links[md]
        
        # Get backlinks for this file
        file_backlinks = backlinks_map.get(stem.lower(), [])
        
        # Dedup backlinks (could have multiple links in the same source file)
        seen = set()
        deduped_backlinks = []
        for s, r in file_backlinks:
            if s.lower() not in seen:
                seen.add(s.lower())
                deduped_backlinks.append((s, r))

        if stem.lower() == "index":
            new_content = content
        else:
            new_content = update_backlinks_in_content(content, deduped_backlinks)
        
        # Only write if content actually changed
        try:
            old_content = md.read_text(encoding="utf-8", errors="replace")
            if old_content.strip() != new_content.strip():
                md.write_text(new_content, encoding="utf-8")
                # print(f"Updated: {md.name}")
                updated_count += 1
        except Exception as e:
            print(f"Error writing to {md.name}: {e}", file=sys.stderr)

    print(f"✓ Completed! Checked {len(file_contents)} files, updated {updated_count} files with link corrections and backlinks.")


if __name__ == "__main__":
    main()
