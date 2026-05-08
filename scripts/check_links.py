"""
Wikilink broken link checker
Usage: python3 check_links.py [--root /path/to/wiki]
"""
import re
import sys
import argparse
from pathlib import Path

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*?)?\]\]")
WIKI_ROOT = Path(__file__).parent.parent


def collect_all_slugs(root: Path) -> set[str]:
    """Collect all existing markdown file paths (without extension)."""
    slugs = set()
    for md in root.rglob("*.md"):
        rel = md.relative_to(root)
        # relative path without extension
        slugs.add(str(rel.with_suffix("")))
        # filename only
        slugs.add(md.stem)
    return slugs


def check_links(root: Path) -> list[dict]:
    all_slugs = collect_all_slugs(root)
    broken = []

    search_dirs = [root / "wiki", root / "garden", root / "clippings"]
    for d in search_dirs:
        if not d.exists():
            continue
        for md in d.rglob("*.md"):
            content = md.read_text(encoding="utf-8")
            for match in WIKILINK_RE.finditer(content):
                target = match.group(1).strip()
                # normalize path: use forward slashes
                target_stem = target.replace("\\", "/")
                if target_stem.endswith(".md"):
                    target_stem = target_stem[:-3]

                # check existence
                exists = (
                    target_stem in all_slugs
                    or Path(target_stem).stem in all_slugs
                    or any(s.endswith(target_stem) for s in all_slugs)
                )
                if not exists:
                    broken.append({
                        "file": str(md.relative_to(root)),
                        "link": target,
                    })

    return broken


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(WIKI_ROOT))
    parser.add_argument("--strict", action="store_true", help="Exit code 1 if broken links found")
    args = parser.parse_args()

    root = Path(args.root)
    broken = check_links(root)

    if not broken:
        print("No broken links found")
        sys.exit(0)

    print(f"Found {len(broken)} broken link(s):\n")
    for item in broken:
        print(f"  {item['file']}: [[{item['link']}]]")

    if args.strict:
        sys.exit(1)
    else:
        print("\n(Build will continue)")
        sys.exit(0)


if __name__ == "__main__":
    main()