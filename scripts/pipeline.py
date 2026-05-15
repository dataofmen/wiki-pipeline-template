#!/usr/bin/env python3
"""
Wiki Pipeline Orchestrator — Python-based pipeline with Claude Code for note generation only.

Phases:
  3. Ingest: Python scripts (fetch_url, fetch_thread, extract_pdf)
  4. Note Maker: Claude Code with --continue (session reuse)
  5. Publisher: Shell commands (sync, build, deploy)
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
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
INBOX_DIR = PROJECT_DIR / "inbox"
SITE_BASE_URL = "https://dataofmen.github.io"
CLIPPINGS_DIR = PROJECT_DIR / "clippings"
WORKSPACE_DIR = PROJECT_DIR / "_workspace"
WIKI_DIR = PROJECT_DIR / "wiki"
GARDEN_DIR = PROJECT_DIR / "garden"
QUARTZ_DIR = PROJECT_DIR / "quartz"
SCRIPTS_DIR = PROJECT_DIR / "scripts"
LOG_FILE = SCRIPTS_DIR / "pipeline.log"
LOCK_FILE = SCRIPTS_DIR / ".pipeline.lock"
VENV_PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python3")
UF_VENV_PYTHON = str(Path("/Users/hmkwon/Projects/ultimate-fetcher/.venv/bin/python3"))

# Try importing ultimate-fetcher bridge; falls back to subprocess via UF venv Python
# fetch_and_save() handles both cases: direct import if available, subprocess otherwise
try:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from fetcher_bridge import fetch_and_save as _fetch_and_save
    USE_ULTIMATE_FETCHER = True
except ImportError:
    USE_ULTIMATE_FETCHER = False
    _fetch_and_save = None

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Also log to stderr for subprocess visibility
handler = logging.StreamHandler(sys.stderr)
handler.setLevel(logging.INFO)
logger.addHandler(handler)


def _note_path_to_url(path: str) -> str:
    """Convert a note file path to its published site URL."""
    p = Path(path)
    # Strip .md extension and build URL from relative path
    relative = p.with_suffix("")
    return f"{SITE_BASE_URL}/{relative}"


def _load_dotenv_value(key: str) -> str:
    """Read a single value from scripts/.env without requiring python-dotenv at module level."""
    env_file = SCRIPTS_DIR / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(errors="ignore").splitlines():
        line = line.strip()
        if line.startswith(key + "=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def notify_telegram(ingest_results: list, note_results: list, publish_success: bool) -> bool:
    """Send pipeline completion summary to Telegram."""
    try:
        import requests as _requests
    except ImportError:
        logger.warning("requests not available — skipping Telegram notification")
        return False

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or _load_dotenv_value("TELEGRAM_BOT_TOKEN")
    allowed_users = os.environ.get("TELEGRAM_ALLOWED_USERS") or _load_dotenv_value("TELEGRAM_ALLOWED_USERS")

    if not token or not allowed_users:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_ALLOWED_USERS not set — skipping notification")
        return False

    chat_ids = [uid.strip() for uid in allowed_users.split(",") if uid.strip()]

    successful_clips = len([r for r in ingest_results if r.get("status") == "success"])
    failed_clips = len([r for r in ingest_results if r.get("status") == "error"])
    publish_icon = "✅" if publish_success else "❌"

    lines = [
        f"{publish_icon} *파이프라인 완료* — {datetime.now().strftime('%m/%d %H:%M')}",
        "",
        f"📥 수신: {len(ingest_results)}개",
        f"📄 클리핑: {successful_clips}개 성공 / {failed_clips}개 실패",
        f"📝 노트: {len(note_results)}개 생성",
        f"🌐 퍼블리시: {'성공' if publish_success else '실패'}",
    ]

    if note_results:
        lines.append("")
        lines.append("*생성된 노트:*")
        for r in note_results[:10]:  # cap at 10 to avoid flood
            path = r.get("path", "")
            if not path:
                continue
            title = r.get("title") or Path(path).stem.replace("-", " ")
            url = _note_path_to_url(path)
            lines.append(f"• [{title}]({url})")
        if len(note_results) > 10:
            lines.append(f"  _외 {len(note_results) - 10}개…_")

    lines += ["", f"🔗 [{SITE_BASE_URL}]({SITE_BASE_URL})"]

    text = "\n".join(lines)
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    all_ok = True
    for chat_id in chat_ids:
        try:
            resp = _requests.post(
                api_url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(f"Telegram notification sent to {chat_id}")
            else:
                logger.warning(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
                all_ok = False
        except Exception as e:
            logger.error(f"Telegram send failed for {chat_id}: {e}")
            all_ok = False

    return all_ok


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and log output."""
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd or PROJECT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    if check and result.returncode != 0:
        logger.error(f"Command failed: {' '.join(cmd)}")
        logger.error(f"stdout: {result.stdout}")
        logger.error(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed with code {result.returncode}: {' '.join(cmd)}")
    return result


def _get_already_clipped_urls() -> set[str]:
    """Return set of URLs already present in clippings/."""
    clipped = set()
    for cf in CLIPPINGS_DIR.glob("*.md"):
        try:
            for line in cf.read_text(errors="ignore").split("\n"):
                if line.startswith("source_url:"):
                    clipped.add(line.split("source_url:", 1)[1].strip())
                    break
        except Exception:
            pass
    return clipped


def _mark_inbox_processed(inbox_path: str) -> None:
    """Update inbox file status from pending/processing → processed."""
    path = Path(inbox_path)
    if not path.exists():
        return
    content = path.read_text(errors="ignore")
    content = content.replace("status: pending", "status: processed")
    content = content.replace("status: processing", "status: processed")
    path.write_text(content)


def scan_inbox() -> list[dict]:
    """Scan inbox for pending files (Phase 2 output or manual drops)."""
    pending = []
    already_clipped = _get_already_clipped_urls()

    # Check _workspace/kiwi_new_files.jsonl first
    kiwi_file = WORKSPACE_DIR / "kiwi_new_files.jsonl"
    if kiwi_file.exists():
        with open(kiwi_file) as f:
            for line in f:
                entry = json.loads(line.strip())
                if entry.get("status") in ("pending", "processing"):
                    inbox_path = PROJECT_DIR / entry["inbox_path"]
                    if inbox_path.exists():
                        source_url = entry.get("source_url", "")
                        if source_url and source_url in already_clipped:
                            logger.info(f"Skipping already-clipped URL: {source_url}")
                            _mark_inbox_processed(str(inbox_path))
                            continue
                        entry["inbox_path_resolved"] = str(inbox_path)
                        entry["status"] = "processing"
                        pending.append(entry)
        logger.info(f"Found {len(pending)} pending items from kiwi")

    # Also scan inbox/ for any unprocessed files
    for subdir in ["urls", "threads", "pdfs"]:
        dir_path = INBOX_DIR / subdir
        if not dir_path.exists():
            continue
        for f in dir_path.glob("*.md"):
            content = f.read_text(errors="ignore")
            if "status: pending" in content or "status: processing" in content:
                # Check if already in pending list
                if not any(p.get("inbox_path", "").endswith(f.name) for p in pending):
                    # Parse frontmatter
                    url = ""
                    for line in content.split("\n"):
                        if line.startswith("source_url:"):
                            url = line.split("source_url:", 1)[1].strip()
                            break
                    if url and url in already_clipped:
                        logger.info(f"Skipping already-clipped URL: {url}")
                        _mark_inbox_processed(str(f))
                        continue
                    entry_type = "thread" if subdir == "threads" else ("pdf" if subdir == "pdfs" else "url")
                    pending.append({
                        "source_url": url,
                        "type": entry_type,
                        "inbox_path": str(f.relative_to(PROJECT_DIR)),
                        "inbox_path_resolved": str(f),
                        "status": "processing",
                        "received_at": datetime.now().isoformat(),
                    })
        # Also handle direct file drops (not .md, like .pdf files)
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
    """Phase 3: Extract content from inbox files → clippings/."""
    logger.info(f"=== Phase 3: Ingest ({len(pending)} items) ===")
    if USE_ULTIMATE_FETCHER:
        logger.info("Using ultimate-fetcher bridge for URL/thread fetching")
    else:
        logger.info("ultimate-fetcher not available, falling back to subprocess scripts")
    results = []
    CLIPPINGS_DIR.mkdir(parents=True, exist_ok=True)

    for item in pending:
        source_type = item.get("type", "url")
        source_url = item.get("source_url", "")
        inbox_path = item.get("inbox_path_resolved", "")

        logger.info(f"Processing: {source_url or inbox_path} (type: {source_type})")

        try:
            # PDF extraction stays as subprocess (ultimate-fetcher has no PDF plugin)
            if source_type == "pdf" and inbox_path:
                python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"
                result = run_cmd(
                    [python_bin, str(SCRIPTS_DIR / "extract_pdf.py"), inbox_path, "--output", str(CLIPPINGS_DIR)],
                    check=False,
                )
                output = result.stdout.strip() if result else ""
                clipping_path = ""
                for line in output.split("\n"):
                    if line.startswith("Saved:") or line.startswith("Clipping saved:"):
                        clipping_path = line.split(":", 1)[1].strip()
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

            # URL/thread: use ultimate-fetcher bridge if available, else subprocess
            elif source_url and source_type in ("url", "thread"):
                if USE_ULTIMATE_FETCHER:
                    fetch_result = _fetch_and_save(source_url, CLIPPINGS_DIR, source_type=source_type)
                    results.append({
                        "source_url": source_url,
                        "clipping_path": fetch_result.get("clipping_path", ""),
                        "status": fetch_result.get("status", "error"),
                        "domain_hint": fetch_result.get("domain_hint", "general"),
                        "fetch_method": fetch_result.get("fetch_method", "unknown"),
                    })
                    logger.info(f"Clipping saved ({fetch_result.get('fetch_method', '?')}): {fetch_result.get('clipping_path', '')}")
                else:
                    # Fallback: subprocess to old scripts
                    if source_type == "thread":
                        result = run_cmd(
                            ["python3", str(SCRIPTS_DIR / "fetch_thread.py"), source_url, "--output", str(CLIPPINGS_DIR)],
                            check=False,
                        )
                    else:
                        result = run_cmd(
                            ["python3", str(SCRIPTS_DIR / "fetch_url.py"), source_url, "--output", str(CLIPPINGS_DIR)],
                            check=False,
                        )
                    output = result.stdout.strip() if result else ""
                    clipping_path = ""
                    for line in output.split("\n"):
                        if line.startswith("Saved:") or line.startswith("Clipping saved:"):
                            clipping_path = line.split(":", 1)[1].strip()
                    if not clipping_path:
                        recent = max(CLIPPINGS_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, default=None)
                        if recent:
                            clipping_path = str(recent)
                    results.append({
                        "source_url": source_url,
                        "clipping_path": clipping_path,
                        "status": "success" if result and result.returncode == 0 else "error",
                        "domain_hint": item.get("domain_hint", "general"),
                    })
                    logger.info(f"Clipping saved: {clipping_path}")
            else:
                logger.warning(f"Unknown type or missing info: {item}")
        except Exception as e:
            logger.error(f"Error processing {source_url or inbox_path}: {e}")
            results.append({
                "source_url": source_url or inbox_path,
                "clipping_path": "",
                "status": "error",
                "domain_hint": item.get("domain_hint", "general"),
            })

        # Mark inbox file processed only on success
        if results and results[-1].get("status") == "success" and inbox_path:
            _mark_inbox_processed(inbox_path)

    # Write ingestor_done.jsonl
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
    with open(done_file, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info(f"Ingest complete: {len([r for r in results if r['status'] == 'success'])} success, {len([r for r in results if r['status'] == 'error'])} error")

    return results


def _is_empty_clipping(path: str) -> bool:
    """Check if a clipping file is empty/failed (no real content)."""
    try:
        content = Path(path).read_text(errors="ignore")
        # Failed extraction markers
        failure_markers = [
            "[스레드 추출 실패",
            "[스레드 추출 실패 — 수동 처리 필요]",
            "[fetch failed]",
            "[원문 추출 실패]",
        ]
        if any(marker in content for marker in failure_markers):
            return True
        # Check if body is essentially empty (front matter only)
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
            # Remove source URL references
            lines = [l for l in body.split("\n") if not l.startswith("원본:") and not l.startswith("*원문")]
            body_text = "\n".join(lines).strip()
            if len(body_text) < 50:  # Less than 50 chars of actual content
                return True
        return False
    except Exception:
        return True


def phase_note_make(clippings: list[dict]) -> list[dict]:
    """Phase 4: Generate notes using Claude Code with session reuse."""
    logger.info(f"=== Phase 4: Note Maker ({len(clippings)} clippings) ===")

    successful_clippings = [c for c in clippings if c.get("status") == "success" and c.get("clipping_path")]
    # Filter out empty/failed clippings
    valid_clippings = [c for c in successful_clippings if not _is_empty_clipping(c["clipping_path"])]
    skipped = len(successful_clippings) - len(valid_clippings)
    if skipped:
        logger.warning(f"Skipping {skipped} empty/failed clippings")
    successful_clippings = valid_clippings
    if not successful_clippings:
        logger.warning("No successful clippings to process")
        return []

    # Build focused prompt for note generation
    clipping_list = "\n".join(
        f"- {c['clipping_path']} (domain: {c.get('domain_hint', 'general')})"
        for c in successful_clippings
    )

    prompt = f"""다음 clipping 파일들을 6 Rs 파이프라인으로 처리하라. note-pipeline 스킬과 note-format.md 참조 파일을 반드시 읽고 따른다.

처리할 clipping 파일들:
{clipping_list}

작업 순서:
1. references/note-format.md 읽기
2. 각 clipping에 대해 R1(Reduce) → R2(Reflect) → R3(Reweave) → R4(Record) → R5(Verify) 실행
3. 각 clipping마다 wiki/ 노트 1개 + garden/ 노트 1~3개 생성
4. wiki/concepts/ 또는 wiki/entities/ 에 저장 (type 기준)
5. 관련 index.md 갱신
6. log.md에 작업 기록
7. 완료 목록을 _workspace/note_maker_done.jsonl에 작성

**위키링크 규칙 (반드시 준수):**
- 위키링크는 순수 슬러그만 사용: `[[슬러그]]` 형식
- path-prefixed 위키링크 금지: `[[concepts/슬러그]]`, `[[entities/슬러그]]` 사용 금지
- 올바른 예: `[[20260515-apple-accidental-moat]]`
- 잘못된 예: `[[concepts/20260515-apple-accidental-moat]]`"""

    # Resolve claude binary — launchd daemon PATH may omit user PATH entries
    _claude_candidates = [
        shutil.which("claude"),
        "/Users/hmkwon/.local/bin/claude",
        "/Applications/cmux.app/Contents/Resources/bin/claude",
        "/opt/homebrew/bin/claude",
        "/usr/local/bin/claude",
    ]
    claude_bin = next((c for c in _claude_candidates if c and Path(c).exists()), None)
    if not claude_bin:
        logger.error("Claude CLI not found in any known location")
        return [{"status": "error", "error": "claude CLI not found"}]
    cmd = [
        claude_bin,
        "-p", prompt,
        "--continue",
        "--fork-session",
        "--allowedTools", "Edit,Write,Bash,Read,Agent",
    ]

    logger.info("Invoking Claude Code for note generation...")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
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

    # Check results by scanning for new files
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
                    if (now - mtime).total_seconds() < 600:  # Created in last 10 min
                        results.append({
                            "path": str(f.relative_to(PROJECT_DIR)),
                            "status": "created",
                        })
        logger.info(f"Found {len(results)} recently created notes")

    return results


def phase_publish() -> bool:
    """Phase 5: Quartz build + deploy."""
    logger.info("=== Phase 5: Publisher ===")

    # Step 1: Sync content
    logger.info("Syncing content...")
    sync_result = run_cmd(["bash", str(SCRIPTS_DIR / "sync_content.sh")], check=False)
    if sync_result.returncode != 0:
        logger.warning(f"Content sync had issues (non-fatal): {sync_result.stderr[:200]}")

    # Step 2: Check links
    logger.info("Checking links...")
    run_cmd(["python3", str(SCRIPTS_DIR / "check_links.py")], check=False)

    # Step 3: Push 0004_Wikis content to GitHub (build pulls from this repo)
    logger.info("Pushing content to GitHub...")
    run_cmd(["git", "add", "wiki/", "garden/", "clippings/", "scripts/", "index.md"],
            cwd=PROJECT_DIR, check=False)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    commit_result = run_cmd(
        ["git", "commit", "-m", f"content: pipeline auto-publish {ts}"],
        cwd=PROJECT_DIR, check=False,
    )
    if commit_result.returncode == 0:
        push_result = run_cmd(["git", "push", "origin", "main"], cwd=PROJECT_DIR, check=False)
        if push_result.returncode != 0:
            logger.warning(f"git push failed (non-fatal): {push_result.stderr[:200]}")
    else:
        logger.info("Nothing new to commit for 0004_Wikis")

    # Step 4: Build and deploy
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
    """Phase 6: Generate summary report and send Telegram notification."""
    logger.info("=== Phase 6: Report ===")

    successful_clips = len([r for r in ingest_results if r.get("status") == "success"])
    failed_clips = len([r for r in ingest_results if r.get("status") == "error"])
    notes_created = len(note_results)

    report = f"""
파이프라인 실행 결과 — {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'=' * 50}
- 수신: {len(ingest_results)}개 항목
- 클리핑: {successful_clips}개 완료 / {failed_clips}개 실패
- 노트 생성: {notes_created}개
- 퍼블리시: {'성공' if publish_success else '실패'}
- 사이트: {SITE_BASE_URL}/
{'=' * 50}
"""
    print(report)
    logger.info(report)

    # Send Telegram notification
    notify_telegram(ingest_results, note_results, publish_success)

    # Rotate workspace
    workspace_prev = PROJECT_DIR / "_workspace_prev"
    if workspace_prev.exists():
        import shutil
        shutil.rmtree(workspace_prev)
    WORKSPACE_DIR.rename(workspace_prev)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # Write summary to new workspace
    with open(WORKSPACE_DIR / "last_run_summary.md", "w") as f:
        f.write(report)


def main():
    # Clear stale lock file at pipeline start
    LOCK_FILE.unlink(missing_ok=True)

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
                ingest_results = []

    # Phase 4: Note Maker
    if 4 in phases:
        if args.dry_run:
            print(f"[DRY RUN] Would invoke Claude Code for note generation")
        else:
            # Use ingestor_done.jsonl if available, otherwise scan clippings
            done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
            if done_file.exists():
                with open(done_file) as f:
                    clippings = [json.loads(line.strip()) for line in f]
            else:
                clippings = ingest_results

            if clippings:
                note_results = phase_note_make(clippings)
            else:
                logger.warning("No clippings to process for note generation")

    # Skip Phase 5 if no notes were created AND Phase 4 was also requested
    if 5 in phases and 4 in phases and not note_results:
        if not args.dry_run:
            logger.warning("No notes created — skipping publish phase to avoid deploying empty content")
            phases.discard(5)

    # Phase 5: Publisher
    if 5 in phases:
        if args.dry_run:
            print("[DRY RUN] Would run Quartz build + deploy")
        else:
            publish_success = phase_publish()

    # Phase 6: Report
    if 6 in phases and not args.dry_run:
        phase_report(ingest_results, note_results, publish_success)

    logger.info("Pipeline complete")


if __name__ == "__main__":
    main()