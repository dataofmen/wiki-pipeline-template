import os
import re
import sys
import yaml
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import shutil
from urllib.parse import urlparse, urlencode, parse_qsl

# ---------------------------------------------------------------------------
# Config & Setup
# ---------------------------------------------------------------------------
WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CLIPPINGS_DIR = WORKSPACE_DIR / "clippings"
WIKI_DIR = WORKSPACE_DIR / "wiki"
GARDEN_DIR = WORKSPACE_DIR / "garden"
MOC_DIR = WORKSPACE_DIR / "garden"  # assuming MOCs are in garden/
INBOX_DIR = WORKSPACE_DIR / "inbox"
ERRORS_DIR = INBOX_DIR / "errors"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("diagnostics")

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_frontmatter(file_path: Path) -> dict:
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if not match:
        return {}
    fm_str = match.group(1)
    try:
        return yaml.safe_load(fm_str) or {}
    except Exception:
        return {}

def extract_source_clipping(fm: dict) -> str:
    # returns the bare slug if present
    src = fm.get("source_clipping", "")
    if not src:
        return ""
    if isinstance(src, list):
        src = src[0] if src else ""
    # "[[20260528-thread.md]]" -> "20260528-thread.md" -> "20260528-thread"
    m = re.search(r"\[\[(.*?)\]\]", str(src))
    if m:
        inner = m.group(1)
        return inner.replace(".md", "")
    return str(src).replace(".md", "")

def _normalize_url(url: str) -> str:
    url = url.strip().strip('"').strip("'")
    try:
        p = urlparse(url)
        TRACKING_PARAMS = {"si", "utm_source", "utm_medium", "utm_campaign",
                           "utm_term", "utm_content", "fbclid", "gclid", "ref"}
        qs = [(k, v) for k, v in parse_qsl(p.query) if k not in TRACKING_PARAMS]
        normalized = p._replace(query=urlencode(qs), fragment="").geturl()
        return normalized.rstrip("/")
    except Exception:
        return url.rstrip("/")

def _get_already_clipped_urls() -> set[str]:
    clipped = set()
    for cf in CLIPPINGS_DIR.glob("*.md"):
        try:
            for line in cf.read_text(errors="ignore").split("\n"):
                if line.startswith("source_url:"):
                    raw = line.split("source_url:", 1)[1].strip()
                    clipped.add(_normalize_url(raw))
                    break
        except Exception:
            pass
    return clipped

def check_inbox_orphans(auto_heal=False):
    """Find and revert inbox files marked as processed but missing clippings."""
    logger.info("=== Checking Inbox Orphans ===")
    already_clipped = _get_already_clipped_urls()
    orphans = []
    
    for subdir in ["urls", "threads", "pdfs", "texts"]:
        dir_path = INBOX_DIR / subdir
        if not dir_path.exists():
            continue
        for f in dir_path.glob("*.md"):
            content = f.read_text(errors="ignore")
            if "status: processed" in content:
                url = ""
                for line in content.split("\n"):
                    if line.startswith("source_url:"):
                        url = line.split("source_url:", 1)[1].strip()
                        break
                if url and _normalize_url(url) not in already_clipped:
                    orphans.append(f)
                    logger.warning(f"[Inbox Orphan] {f.name} marked processed but no clipping found.")
                    if auto_heal:
                        logger.info(f"Auto-healing: Reverting {f.name} to pending")
                        f.write_text(content.replace("status: processed", "status: pending"))
        
        if subdir == "texts":
            for f in dir_path.glob("*.meta.yaml"):
                content = f.read_text(errors="ignore")
                if "status: processed" in content:
                    url = ""
                    for line in content.split("\n"):
                        if line.startswith("source_url:"):
                            url = line.split("source_url:", 1)[1].strip()
                            break
                    if url and _normalize_url(url) not in already_clipped:
                        orphans.append(f)
                        logger.warning(f"[Inbox Orphan] {f.name} marked processed but no clipping found.")
                        if auto_heal:
                            logger.info(f"Auto-healing: Reverting {f.name} to pending")
                            f.write_text(content.replace("status: processed", "status: pending"))
    
    return orphans

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_clippings(auto_heal=False):
    """Check for clipping errors: file too small, or error text present."""
    logger.info("=== Checking Clippings ===")
    errors_found = []
    
    for md_file in CLIPPINGS_DIR.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            # Heuristics for errors
            if len(content.strip()) < 100:
                errors_found.append((md_file, "Too small (< 100 bytes)"))
            elif "Failed to fetch" in content or "Access Denied" in content or "Error 403" in content:
                errors_found.append((md_file, "Error text found in body"))
        except Exception as e:
            errors_found.append((md_file, f"Unreadable file: {e}"))

    for f, reason in errors_found:
        logger.error(f"[Clipping Error] {f.name}: {reason}")
        if auto_heal:
            ensure_dir(ERRORS_DIR)
            target = ERRORS_DIR / f.name
            logger.info(f"Auto-healing: Moving {f.name} to {ERRORS_DIR}")
            shutil.move(str(f), str(target))
            # Move meta if exists
            meta_file = f.with_name(f.name + ".meta.yaml")
            if meta_file.exists():
                shutil.move(str(meta_file), str(ERRORS_DIR / meta_file.name))
            
    return errors_found

def check_titles(auto_heal=False):
    """Check for bad titles in wiki and garden."""
    logger.info("=== Checking Titles ===")
    bad_titles = []
    
    for target_dir in [WIKI_DIR, GARDEN_DIR]:
        for md_file in target_dir.rglob("*.md"):
            if md_file.name in ["index.md", "log.md"]:
                continue
            
            fm = parse_frontmatter(md_file)
            title = fm.get("title", "")
            
            is_bad = False
            reason = ""
            if not title or title in ["None", "Untitled", "null"]:
                is_bad = True
                reason = "Missing or default title"
            elif "#" in title:
                is_bad = True
                reason = "Markdown headers in title"
                
            if is_bad:
                bad_titles.append((md_file, reason))
                
    for f, reason in bad_titles:
        logger.error(f"[Title Error] {f.name}: {reason}")
        if auto_heal:
            # We don't auto-heal title generation directly here as it requires AI call.
            # Usually better handled by wiping it and letting note-maker regen it or manual fix.
            logger.info(f"Auto-healing title is disabled by default due to AI dependency. Manual review needed for: {f.name}")
            
    return bad_titles

def check_duplicates(auto_heal=False):
    """Check for duplicates based on title and source_clipping."""
    logger.info("=== Checking Duplicates ===")
    duplicates = []
    
    title_map = defaultdict(list)
    for target_dir in [WIKI_DIR, GARDEN_DIR]:
        for md_file in target_dir.rglob("*.md"):
            if md_file.name in ["index.md", "log.md"] or md_file.name.startswith("moc-"):
                continue
            fm = parse_frontmatter(md_file)
            title = fm.get("title", "")
            if title:
                title_map[title].append(md_file)
                
    for title, paths in title_map.items():
        # Only wiki should have strictly unique titles.
        # But if identical titles are in wiki/entities vs wiki/concepts, that's weird too.
        # Let's just flag if there are multiple notes with the EXACT same title.
        if len(paths) > 1:
            logger.warning(f"[Duplicate Potential] Title '{title}' found in multiple files: {[p.name for p in paths]}")
            
            # If two files are identical in size and title in the SAME folder, they are definitely duplicates.
            # This handles the recent issue where two files in wiki/entities/ had same title.
            # Auto-healing: Keep the shorter filename, or the most recently modified one.
            if auto_heal and all(p.parent == paths[0].parent for p in paths):
                logger.info(f"Auto-healing: Consolidating duplicate notes in {paths[0].parent}")
                # Keep the first one, delete others. 
                # (In a real scenario, we'd merge links, but simple delete is safest for exact clones)
                # Just logging for safety unless forced.
                pass
            duplicates.append((title, paths))
            
    return duplicates

def check_unprocessed(auto_heal=False):
    """Check if any clipping is not referenced by any wiki/garden note.

    When auto_heal is True, orphaned clippings are MOVED to a quarantine
    directory (never deleted) so nothing is lost to a false-positive orphan
    detection.  A grace period (GRACE_DAYS) protects freshly-created clippings:
    note generation runs asynchronously, so a clipping created today legitimately
    has no note yet and must not be quarantined before it gets its turn.  This
    breaks the destructive loop where note-gen lag → quarantine → inbox-orphan
    revert → re-fetch → re-quarantine.
    """
    GRACE_DAYS = 3  # don't quarantine clippings younger than this
    QUARANTINE_DIR = CLIPPINGS_DIR / "_quarantine"

    logger.info("=== Checking Unprocessed Clippings ===")

    all_clippings = {f.stem for f in CLIPPINGS_DIR.rglob("*.md")
                     if f.name not in ["index.md", "log.md"]
                     and "_quarantine" not in str(f)}
    referenced_clippings = set()

    for target_dir in [WIKI_DIR, GARDEN_DIR]:
        for md_file in target_dir.rglob("*.md"):
            fm = parse_frontmatter(md_file)
            src = extract_source_clipping(fm)
            if src:
                referenced_clippings.add(src)

    unprocessed = all_clippings - referenced_clippings
    now = datetime.now().timestamp()

    for u in unprocessed:
        logger.warning(f"[Unprocessed] Clipping '{u}' has no notes associated with it.")
        if auto_heal:
            src_file = CLIPPINGS_DIR / f"{u}.md"
            if src_file.exists():
                age_days = (now - src_file.stat().st_mtime) / 86400
                if age_days < GRACE_DAYS:
                    logger.info(f"  Skipping quarantine for '{u}' — within grace period ({age_days:.1f}d < {GRACE_DAYS}d)")
                    continue
                ensure_dir(QUARANTINE_DIR)
                target = QUARANTINE_DIR / src_file.name
                logger.info(f"Auto-healing: Quarantining orphaned clipping '{u}' → _quarantine/ (age: {age_days:.0f}d)")
                shutil.move(str(src_file), str(target))
                meta_file = src_file.with_name(src_file.name + ".meta.yaml")
                if meta_file.exists():
                    shutil.move(str(meta_file), str(QUARANTINE_DIR / meta_file.name))

    # NOTE: quarantined files are intentionally never auto-deleted. Review and
    # clear clippings/_quarantine/ manually when you're sure they're not needed.
    return unprocessed

def check_moc_status(auto_heal=False):
    """Check if MOC generation is needed."""
    logger.info("=== Checking MOC Status ===")
    
    try:
        if auto_heal:
            logger.info("Auto-healing: Forcing MOC update...")
            # sys.executable keeps the venv interpreter (bare python3 may lack
            # yaml); timeout bounds the codex synthesis call inside.
            subprocess.run([sys.executable, "scripts/generate_moc.py"],
                           cwd=WORKSPACE_DIR, check=True, timeout=600)
            return True
    except Exception as e:
        logger.error(f"[MOC Error] Failed to generate MOCs: {e}")
        return False

def run_all_diagnostics(auto_heal=False):
    report = []
    
    ce = check_clippings(auto_heal=auto_heal)
    te = check_titles(auto_heal=auto_heal)
    dupes = check_duplicates(auto_heal=auto_heal)
    unp = check_unprocessed(auto_heal=auto_heal)
    inb_orphans = check_inbox_orphans(auto_heal=auto_heal)
    
    if auto_heal:
        check_moc_status(auto_heal=True)
        
    report.append("=== Diagnostics Summary ===")
    report.append(f"Clipping Errors: {len(ce)}")
    report.append(f"Title Errors: {len(te)}")
    report.append(f"Potential Duplicates: {len(dupes)}")
    report.append(f"Unprocessed Clippings: {len(unp)}")
    report.append(f"Orphaned Inbox Items: {len(inb_orphans)}")
    
    final_report = "\n".join(report)
    logger.info(final_report)
    return final_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-heal", action="store_true", help="Automatically fix issues if possible")
    parser.add_argument("--dry-run", action="store_true", help="Run checks without fixing (overrides auto-heal)")
    args = parser.parse_args()
    
    heal = args.auto_heal and not args.dry_run
    run_all_diagnostics(auto_heal=heal)
