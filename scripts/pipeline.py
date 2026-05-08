#!/usr/bin/env python3
"""
Wiki Pipeline Orchestrator — Python-based pipeline with Claude Code for note generation only.

Phases:
  3. Ingest: Python scripts (fetch_url, fetch_thread, extract_pdf)
  4. Note Maker: Claude Code with --continue (session reuse)
  5. Publisher: Shell commands (sync, commit, build, deploy)
  6. Report: Summary generation

Usage:
  python3 scripts/pipeline.py              # Full pipeline (phases 3-6)
  python3 scripts/pipeline.py --phase 3    # Ingest only
  python3 scripts/pipeline.py --phase 4    # Notes only
  python3 scripts/pipeline.py --phase 5    # Publish only
  python3 scripts/pipeline.py --phase 6    # Report only
  python3 scripts/pipeline.py --phase 3-5  # Ingest through publish
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
INBOX_DIR = PROJECT_DIR / "inbox"
CLIPPINGS_DIR = PROJECT_DIR / "clippings"
WORKSPACE_DIR = PROJECT_DIR / "_workspace"
WIKI_DIR = PROJECT_DIR / "wiki"
GARDEN_DIR = PROJECT_DIR / "garden"
QUARTZ_DIR = PROJECT_DIR / "quartz"
SCRIPTS_DIR = PROJECT_DIR / "scripts"
LOG_FILE = SCRIPTS_DIR / "pipeline.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.INFO)
logger.addHandler(handler)


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and log output."""
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd or PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    if check and result.returncode != 0:
        logger.error(f"Command failed: {' '.join(cmd)}")
        raise RuntimeError(f"Command failed with code {result.returncode}: {' '.join(cmd)}")
    return result


def scan_inbox() -> list[dict]:
    """Scan inbox for pending files (Phase 2 output or manual drops)."""
    pending = []

    # Check _workspace/hermes_new_files.jsonl first
    hermes_file = WORKSPACE_DIR / "hermes_new_files.jsonl"
    if hermes_file.exists():
        with open(hermes_file) as f:
            for line in f:
                entry = json.loads(line.strip())
                if entry.get("status") in ("pending", "processing"):
                    inbox_path = PROJECT_DIR / entry["inbox_path"]
                    if inbox_path.exists():
                        entry["inbox_path_resolved"] = str(inbox_path)
                        entry["status"] = "processing"
                        pending.append(entry)
        logger.info(f"Found {len(pending)} pending items from hermes")

    # Also scan inbox/ for any unprocessed files
    for subdir in ["urls", "threads", "pdfs"]:
        dir_path = INBOX_DIR / subdir
        if not dir_path.exists():
            continue
        for f in dir_path.glob("*.md"):
            content = f.read_text(errors="ignore")
            if "status: pending" in content or "status: processing" in content:
                if not any(p.get("inbox_path", "").endswith(f.name) for p in pending):
                    url = ""
                    for line in content.split("\n"):
                        if line.startswith("source_url:"):
                            url = line.split("source_url:", 1)[1].strip()
                            break
                    entry_type = "thread" if subdir == "threads" else ("pdf" if subdir == "pdfs" else "url")
                    pending.append({
                        "source_url": url,
                        "type": entry_type,
                        "inbox_path": str(f.relative_to(PROJECT_DIR)),
                        "inbox_path_resolved": str(f),
                        "status": "processing",
                        "received_at": datetime.now().isoformat(),
                    })
        # Also handle direct PDF file drops
        if subdir == "pdfs":
            for f in dir_path.glob("*.pdf"):
                if not any(p.get("inbox_path", "").endswith(f.name) for p in pending):
                    pending.append({
                        "source_url": str(f),
                        "type": "pdf",
                        "inbox_path": str(f.relative_to(PROJECT_DIR)),
                        "inbox_path_resolved": str(f),
                        "status": "processing",
                        "received_at": datetime.now().isoformat(),
                    })

    logger.info(f"Total pending items: {len(pending)}")
    return pending


def phase_ingest(pending: list[dict]) -> list[dict]:
    """Phase 3: Extract content from inbox files to clippings/."""
    logger.info(f"=== Phase 3: Ingest ({len(pending)} items) ===")
    results = []
    CLIPPINGS_DIR.mkdir(parents=True, exist_ok=True)

    for item in pending:
        source_type = item.get("type", "url")
        source_url = item.get("source_url", "")
        inbox_path = item.get("inbox_path_resolved", "")

        logger.info(f"Processing: {source_url or inbox_path} (type: {source_type})")

        try:
            if source_type == "thread" and source_url:
                result = run_cmd(
                    ["python3", str(SCRIPTS_DIR / "fetch_thread.py"), source_url, "--output", str(CLIPPINGS_DIR)],
                    check=False,
                )
            elif source_type == "url" and source_url:
                result = run_cmd(
                    ["python3", str(SCRIPTS_DIR / "fetch_url.py"), source_url, "--output", str(CLIPPINGS_DIR)],
                    check=False,
                )
            elif source_type == "pdf" and inbox_path:
                result = run_cmd(
                    ["python3", str(SCRIPTS_DIR / "extract_pdf.py"), inbox_path, "--output", str(CLIPPINGS_DIR)],
                    check=False,
                )
            else:
                logger.warning(f"Unknown type or missing info: {item}")
                continue

            # Extract clipping path from script output
            output = result.stdout.strip() if result else ""
            clipping_path = ""
            for line in output.split("\n"):
                if line.startswith("Saved:") or line.startswith("Clipping saved:"):
                    clipping_path = line.split(":", 1)[1].strip()

            # Fallback: find most recent clipping
            if not clipping_path:
                recent = max(CLIPPINGS_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, default=None)
                if recent:
                    clipping_path = str(recent)

            results.append({
                "source_url": source_url or inbox_path,
                "clipping_path": clipping_path,
                "status": "success" if result and result.returncode == 0 else "error",
                "domain_hint": item.get("domain_hint", "general"),
            })
            logger.info(f"Clipping saved: {clipping_path}")

        except Exception as e:
            logger.error(f"Failed to process {source_url or inbox_path}: {e}")
            results.append({
                "source_url": source_url or inbox_path,
                "clipping_path": "",
                "status": "error",
                "error": str(e),
            })

    # Write ingestor_done.jsonl
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
    with open(done_file, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info(f"Ingest complete: {sum(1 for r in results if r['status'] == 'success')} success, {sum(1 for r in results if r['status'] == 'error')} error")

    return results


def phase_note_make(clippings: list[dict]) -> list[dict]:
    """Phase 4: Generate notes using Claude Code with session reuse."""
    logger.info(f"=== Phase 4: Note Maker ({len(clippings)} clippings) ===")

    successful_clippings = [c for c in clippings if c.get("status") == "success" and c.get("clipping_path")]
    if not successful_clippings:
        logger.warning("No successful clippings to process")
        return []

    # Build focused prompt for note generation
    clipping_list = "\n".join(
        f"- {c['clipping_path']} (domain: {c.get('domain_hint', 'general')})"
        for c in successful_clippings
    )

    prompt = f"""Process these clipping files through the 6 Rs pipeline. Read the note-pipeline skill and note-format.md reference first.

Clipping files:
{clipping_list}

Steps:
1. Read references/note-format.md
2. For each clipping: R1(Reduce) -> R2(Reflect) -> R3(Reweave) -> R4(Record) -> R5(Verify)
3. Create wiki/ note (1 per clipping) + garden/ notes (1-3 per clipping)
4. Save to wiki/concepts/ or wiki/entities/ based on type
5. Update related index.md files
6. Append to log.md
7. Write _workspace/note_maker_done.jsonl with created note paths

After creating notes, also update clippings/index.md to include ALL clipping files with their metadata (title, source, date, domain)."""

    # Use --continue for session context, --fork-session to avoid polluting interactive session
    cmd = [
        "claude",
        "-p", prompt,
        "--continue",
        "--fork-session",
    ]

    logger.info("Invoking Claude Code for note generation...")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
        logger.info(f"Claude Code exited with code {result.returncode}")
        if result.stdout:
            logger.info(f"Claude output (first 500 chars): {result.stdout[:500]}")
        if result.stderr:
            logger.warning(f"Claude stderr (first 500 chars): {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        logger.error("Claude Code timed out after 600 seconds")
        return [{"status": "error", "error": "timeout"}]
    except FileNotFoundError:
        logger.error("Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code")
        return [{"status": "error", "error": "claude CLI not found"}]

    # Check results
    results = []
    note_maker_done = WORKSPACE_DIR / "note_maker_done.jsonl"
    if note_maker_done.exists():
        with open(note_maker_done) as f:
            for line in f:
                results.append(json.loads(line.strip()))
        logger.info(f"Note maker completed: {len(results)} notes")
    else:
        # Fallback: scan for recently created files
        logger.warning("note_maker_done.jsonl not found, scanning for recent files")
        now = datetime.now()
        for pattern_dir in [WIKI_DIR / "concepts", WIKI_DIR / "entities", GARDEN_DIR]:
            if pattern_dir.exists():
                for f in pattern_dir.glob("*.md"):
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if (now - mtime).total_seconds() < 600:
                        results.append({
                            "path": str(f.relative_to(PROJECT_DIR)),
                            "status": "created",
                        })
        logger.info(f"Found {len(results)} recently created notes")

    return results


def phase_publish() -> bool:
    """Phase 5: Commit content, sync, build, and deploy."""
    logger.info("=== Phase 5: Publisher ===")

    # Step 0: Commit content changes to content repo (CRITICAL)
    logger.info("Committing content changes...")
    try:
        subprocess.run(["git", "add", "clippings/", "wiki/", "garden/", "index.md", "log.md"],
                       cwd=str(PROJECT_DIR), capture_output=True, check=False)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                cwd=str(PROJECT_DIR), capture_output=True, check=False)
        if result.returncode != 0:  # There are staged changes
            subprocess.run(
                ["git", "commit", "-m", f"Pipeline: content update {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                cwd=str(PROJECT_DIR), capture_output=True, check=False,
            )
            subprocess.run(["git", "push", "origin", "main"],
                           cwd=str(PROJECT_DIR), capture_output=True, check=False)
            logger.info("Content repo committed and pushed")
        else:
            logger.info("No content changes to commit")
    except Exception as e:
        logger.warning(f"Content repo commit failed (non-fatal): {e}")

    # Step 1: Sync content to Quartz
    logger.info("Syncing content...")
    sync_result = run_cmd(["bash", str(SCRIPTS_DIR / "sync_content.sh")], check=False)
    if sync_result.returncode != 0:
        logger.warning(f"Content sync had issues (non-fatal): {sync_result.stderr[:200]}")

    # Step 2: Check links
    logger.info("Checking links...")
    run_cmd(["python3", str(SCRIPTS_DIR / "check_links.py")], check=False)

    # Step 3: Build and deploy
    logger.info("Building and deploying Quartz site...")
    deploy_result = run_cmd(
        ["npx", "quartz", "sync"],
        cwd=QUARTZ_DIR,
        check=False,
    )

    success = deploy_result.returncode == 0
    if success:
        logger.info("Publish complete!")
    else:
        logger.error(f"Publish failed: {deploy_result.stderr[:500]}")

    # Write publisher_done.json
    done_data = {
        "timestamp": datetime.now().isoformat(),
        "status": "success" if success else "error",
        "deploy_output": deploy_result.stdout[:500] if deploy_result.stdout else "",
    }
    with open(WORKSPACE_DIR / "publisher_done.json", "w") as f:
        json.dump(done_data, f, ensure_ascii=False, indent=2)

    return success


def phase_report(ingest_results: list, note_results: list, publish_success: bool):
    """Phase 6: Generate summary report."""
    logger.info("=== Phase 6: Report ===")

    successful_clips = len([r for r in ingest_results if r.get("status") == "success"])
    failed_clips = len([r for r in ingest_results if r.get("status") == "error"])
    notes_created = len(note_results)

    report = f"""
Pipeline result — {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'=' * 50}
- Received: {len(ingest_results)} items
- Clipped: {successful_clips} success / {failed_clips} error
- Notes: {notes_created} created
- Published: {'success' if publish_success else 'failed'}
{'=' * 50}
"""
    print(report)
    logger.info(report)

    # Rotate workspace
    workspace_prev = PROJECT_DIR / "_workspace_prev"
    if workspace_prev.exists():
        import shutil
        shutil.rmtree(workspace_prev)
    WORKSPACE_DIR.rename(workspace_prev)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    with open(WORKSPACE_DIR / "last_run_summary.md", "w") as f:
        f.write(report)


def main():
    parser = argparse.ArgumentParser(description="Wiki Pipeline Orchestrator")
    parser.add_argument("--phase", type=str, default="3-6",
                        help="Phase(s) to run: 3, 4, 5, 6, or range like 3-5 (default: 3-6)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without executing")
    args = parser.parse_args()

    # Parse phase range
    phases = set()
    if "-" in args.phase:
        start, end = args.phase.split("-")
        phases = set(range(int(start), int(end) + 1))
    else:
        phases = {int(args.phase)}

    logger.info(f"Pipeline starting — phases: {sorted(phases)}")
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    ingest_results = []
    note_results = []
    publish_success = False

    # Phase 3: Ingest
    if 3 in phases:
        if args.dry_run:
            pending = scan_inbox()
            print(f"[DRY RUN] Would process {len(pending)} items from inbox")
        else:
            pending = scan_inbox()
            if pending:
                ingest_results = phase_ingest(pending)
            else:
                logger.info("No pending items in inbox")

    # Phase 4: Note Maker
    if 4 in phases:
        if args.dry_run:
            print("[DRY RUN] Would invoke Claude Code for note generation")
        else:
            done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
            if done_file.exists():
                with open(done_file) as f:
                    clippings = [json.loads(line.strip()) for line in f]
            else:
                clippings = ingest_results

            if clippings:
                note_results = phase_note_make(clippings)

    # Phase 5: Publisher
    if 5 in phases:
        if args.dry_run:
            print("[DRY RUN] Would commit content, sync, build and deploy")
        else:
            publish_success = phase_publish()

    # Phase 6: Report
    if 6 in phases and not args.dry_run:
        phase_report(ingest_results, note_results, publish_success)

    logger.info("Pipeline complete")


if __name__ == "__main__":
    main()