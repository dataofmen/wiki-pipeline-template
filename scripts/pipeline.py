#!/usr/bin/env python3
"""
Wiki Pipeline Orchestrator — Python-based pipeline with Codex CLI for note generation only.

Phases:
  3. Ingest: Python scripts (fetch_url, fetch_thread, extract_pdf)
  4. Note Maker: Codex CLI (non-interactive, ephemeral session)
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
from logging.handlers import RotatingFileHandler
import os
import shutil
import subprocess
import sys
import re
import time
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
UF_VENV_PYTHON = str(Path.home() / "Projects" / "ultimate-fetcher" / ".venv" / "bin" / "python3")

# Try importing ultimate-fetcher bridge; falls back to subprocess via UF venv Python
# fetch_and_save() handles both cases: direct import if available, subprocess otherwise
try:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from fetcher_bridge import fetch_and_save as _fetch_and_save
    USE_ULTIMATE_FETCHER = True
except ImportError:
    USE_ULTIMATE_FETCHER = False
    _fetch_and_save = None

log_handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    handlers=[log_handler],
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


def _publish_label(publish_success) -> tuple[str, str]:
    """Return (icon, label) for publish status. publish_success: True/False/None(skipped)."""
    if publish_success is True:
        return "✅", "성공"
    elif publish_success is None:
        return "⏭️", "건너뜀"
    else:
        return "❌", "실패"


def notify_telegram(ingest_results: list, note_results: list, publish_success) -> bool:
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
    publish_icon, publish_text = _publish_label(publish_success)
    pipeline_icon = publish_icon if publish_success is not True else "✅"

    lines = [
        f"{pipeline_icon} *파이프라인 완료* — {datetime.now().strftime('%m/%d %H:%M')}",
        "",
        f"📥 수신: {len(ingest_results)}개",
        f"📄 클리핑: {successful_clips}개 성공 / {failed_clips}개 실패",
        f"📝 노트: {len(note_results)}개 생성",
        f"🌐 퍼블리시: {publish_text}",
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


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True, env: dict | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run a command and log output. Times out after `timeout` seconds so a hung
    child (e.g. an extractor CLI waiting on interactive input) cannot stall the
    whole pipeline while holding the run lock."""
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or PROJECT_DIR,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout}s: {' '.join(cmd)}")
        result = subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr=f"timeout after {timeout}s")
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


def _normalize_url(url: str) -> str:
    """Strip quotes, fragments, and tracking-only query params for dedup comparison."""
    from urllib.parse import urlparse, urlencode, parse_qsl
    url = url.strip().strip('"').strip("'")
    try:
        p = urlparse(url)
        # Remove tracking-only params that change per share link
        TRACKING_PARAMS = {"si", "utm_source", "utm_medium", "utm_campaign",
                           "utm_term", "utm_content", "fbclid", "gclid", "ref"}
        qs = [(k, v) for k, v in parse_qsl(p.query) if k not in TRACKING_PARAMS]
        normalized = p._replace(query=urlencode(qs), fragment="").geturl()
        return normalized.rstrip("/")
    except Exception:
        return url.rstrip("/")


def _is_empty_clipping(path: str) -> bool:
    """Check if a clipping file is empty/failed (no real content)."""
    try:
        content = Path(path).read_text(errors="ignore")
        # Failed extraction markers
        failure_markers = [
            "[스레드 추출 실패",
            "[스레드 추출 실패 — 수동 처리 필요]",
            "[원문 추출 실패]",
            "[PDF 추출 실패]",
            "[PDF 추출 실패 — 수동 처리 필요]",
            "[fetch failed]",
            "The request could not be satisfied",
            "Warning: Target URL returned error",
            "Request blocked",
            "403 Forbidden",
            "403 ERROR",
            "cloudfront (CloudFront)",
            "권혁민님, 무슨 생각을 하고 계신가요?",
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


def _get_already_clipped_urls() -> set[str]:
    """Return normalized set of URLs already present in clippings/."""
    clipped = set()
    for cf in CLIPPINGS_DIR.glob("*.md"):
        try:
            if _is_empty_clipping(str(cf)):
                continue
            for line in cf.read_text(errors="ignore").split("\n"):
                if line.startswith("source_url:"):
                    raw = line.split("source_url:", 1)[1].strip()
                    clipped.add(_normalize_url(raw))
                    break
        except Exception:
            pass
    return clipped


def _mark_inbox_processed(inbox_path: str) -> None:
    """Update inbox file status from pending/processing → processed."""
    path = Path(inbox_path)
    if not path.exists():
        return
        
    if not path.name.endswith(".yaml"):
        meta_path = path.with_name(path.name + ".meta.yaml")
        if meta_path.exists():
            path = meta_path
            
    if path.suffix.lower() in [".pdf", ".jpg", ".png", ".mp4", ".zip"]:
        return
        
    content = path.read_text(errors="ignore")
    content = content.replace("status: pending", "status: processed")
    content = content.replace("status: processing", "status: processed")
    path.write_text(content)


def scan_inbox() -> list[dict]:
    """Scan inbox for pending files (Phase 2 output or manual drops)."""
    pending = []
    already_clipped = _get_already_clipped_urls()

    # Check inbox/kiwi_new_files.jsonl first
    kiwi_file = INBOX_DIR / "kiwi_new_files.jsonl"
    if kiwi_file.exists():
        processing_file = INBOX_DIR / "kiwi_new_files_processing.jsonl"
        try:
            # Atomic rename to prevent race conditions with kiwi_bot appends
            kiwi_file.rename(processing_file)
        except Exception as e:
            logger.warning(f"Could not rename kiwi status file: {e}")
            processing_file = kiwi_file

        if processing_file.exists():
            with open(processing_file) as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                    except Exception:
                        continue
                    if entry.get("status") in ("pending", "processing"):
                        raw_path = entry.get("inbox_path") or entry.get("path") or entry.get("file_path")
                        if not raw_path: continue
                        inbox_path = PROJECT_DIR / raw_path
                        if inbox_path.exists():
                            source_url = entry.get("source_url", "")
                            if not source_url and inbox_path.suffix == ".md":
                                content = inbox_path.read_text(errors="ignore")
                                for line in content.split("\n"):
                                    if line.startswith("source_url:"):
                                        source_url = line.split("source_url:", 1)[1].strip()
                                        break
                                entry["source_url"] = source_url

                            if source_url and _normalize_url(source_url) in already_clipped:
                                logger.info(f"Skipping already-clipped URL: {source_url}")
                                _mark_inbox_processed(str(inbox_path))
                                continue
                            entry["inbox_path_resolved"] = str(inbox_path)
                            entry["status"] = "processing"
                            pending.append(entry)
            
            # Clean up the processed status file
            try:
                processing_file.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Could not delete processing file: {e}")
        logger.info(f"Found {len(pending)} pending items from kiwi")

    # Also scan inbox/ for any unprocessed files
    for subdir in ["urls", "threads", "pdfs", "texts", "screenshots"]:
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
                    if url and _normalize_url(url) in already_clipped:
                        logger.info(f"Skipping already-clipped URL: {url}")
                        _mark_inbox_processed(str(f))
                        continue
                    entry_type = "image" if subdir == "screenshots" else ("thread" if subdir == "threads" else ("pdf" if subdir == "pdfs" else ("text" if subdir == "texts" else "url")))
                    pending.append({
                        "source_url": url,
                        "type": entry_type,
                        "inbox_path": str(f.relative_to(PROJECT_DIR)),
                        "inbox_path_resolved": str(f),
                        "status": "processing",
                        "received_at": datetime.now().isoformat(),
                    })
        # Also handle direct file drops (not .md, like .pdf or .txt files)
        if subdir == "texts":
            for f in dir_path.glob("*.txt"):
                # meta file may be named either {stem}.meta.yaml or {name}.meta.yaml
                meta_path = f.parent / f"{f.name}.meta.yaml"
                if not meta_path.exists():
                    meta_path = f.parent / f"{f.stem}.meta.yaml"
                if not any(p.get("inbox_path", "").endswith(f.name) for p in pending):
                    source_url, title = "", f.stem.replace("_", " ")
                    meta_status = ""
                    if meta_path.exists():
                        for line in meta_path.read_text(errors="ignore").split("\n"):
                            if line.startswith("source_url:"):
                                source_url = line.split("source_url:", 1)[1].strip()
                            if line.startswith("title:"):
                                title = line.split("title:", 1)[1].strip()
                            if line.startswith("status:"):
                                meta_status = line.split("status:", 1)[1].strip()
                    # Skip if meta.yaml says processed
                    if meta_status == "processed":
                        continue
                    # Skip if URL already has a clipping
                    if source_url and _normalize_url(source_url) in already_clipped:
                        logger.info(f"Skipping already-clipped txt: {source_url}")
                        if meta_path.exists():
                            _mark_inbox_processed(str(meta_path))
                        continue
                    pending.append({
                        "source_url": source_url,
                        "title": title,
                        "type": "text",
                        "inbox_path": str(f.relative_to(PROJECT_DIR)),
                        "inbox_path_resolved": str(f),
                        "status": "processing",
                        "received_at": datetime.now().isoformat(),
                    })
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
        if subdir == "screenshots":
            for ext in ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp"]:
                for f in dir_path.glob(ext):
                    # Images carry no inline status — consult the sidecar meta.yaml
                    # so processed/archived screenshots aren't re-ingested every run.
                    meta_path = f.parent / f"{f.name}.meta.yaml"
                    if meta_path.exists():
                        meta_content = meta_path.read_text(errors="ignore")
                        if "status: pending" not in meta_content and "status: processing" not in meta_content:
                            continue
                    if not any(p.get("inbox_path", "").endswith(f.name) for p in pending):
                        pending.append({
                            "source_url": str(f),
                            "type": "image",
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
            # Text file (transcript, memo) → direct clipping without fetching
            if source_type == "text" and inbox_path and Path(inbox_path).exists():
                from fetcher_bridge import format_clipping, detect_domain_hint
                content = Path(inbox_path).read_text(errors="ignore").strip()
                title = item.get("title") or Path(inbox_path).stem.replace("_", " ")
                domain_hint = detect_domain_hint(source_url or "", title, content)
                # Skip first line if it's a URL (already captured as source_url)
                lines = content.splitlines()
                if lines and lines[0].strip().startswith("http"):
                    content = "\n".join(lines[1:]).strip()

                clipping_path = format_clipping(
                    source_url or inbox_path, title, content,
                    "text-file", "text", domain_hint, CLIPPINGS_DIR,
                )
                # .txt files: mark the companion .meta.yaml as processed
                meta_path = Path(inbox_path).with_name(Path(inbox_path).name + ".meta.yaml")
                if meta_path.exists():
                    _mark_inbox_processed(str(meta_path))
                else:
                    _mark_inbox_processed(inbox_path)
                results.append({
                    "source_url": source_url or inbox_path,
                    "clipping_path": str(clipping_path),
                    "status": "success",
                    "domain_hint": domain_hint,
                    "fetch_method": "text-file",
                })
                logger.info(f"Text clipping saved: {clipping_path}")

            # PDF extraction stays as subprocess (ultimate-fetcher has no PDF plugin)
            elif source_type == "pdf" and inbox_path:
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
                if not clipping_path and result and result.returncode == 0:
                    recent = max(CLIPPINGS_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, default=None)
                    if recent:
                        clipping_path = str(recent)
                results.append({
                    "source_url": source_url or inbox_path,
                    "clipping_path": clipping_path,
                    "status": "success" if clipping_path else "error",
                    "domain_hint": "general",
                    "fetch_method": "pdfplumber",
                })
                if clipping_path:
                    logger.info(f"PDF clipping saved: {clipping_path}")
                    meta_path = Path(inbox_path).with_name(Path(inbox_path).name + ".meta.yaml")
                    if meta_path.exists():
                        _mark_inbox_processed(str(meta_path))
                    else:
                        _mark_inbox_processed(inbox_path)

            # Image extraction via Vision API
            elif source_type == "image" and inbox_path:
                python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"
                result = run_cmd(
                    [python_bin, str(SCRIPTS_DIR / "extract_image.py"), inbox_path, "--output", str(CLIPPINGS_DIR)],
                    check=False,
                )
                output = result.stdout.strip() if result else ""
                clipping_path = ""
                for line in output.split("\n"):
                    if line.startswith("Saved:"):
                        clipping_path = str(CLIPPINGS_DIR / line.split(":", 1)[1].strip())
                if not clipping_path and result and result.returncode == 0:
                    recent = max(CLIPPINGS_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, default=None)
                    if recent:
                        clipping_path = str(recent)
                results.append({
                    "source_url": source_url or inbox_path,
                    "clipping_path": clipping_path,
                    "status": "success" if clipping_path else "error",
                    "domain_hint": "general",
                    "fetch_method": "openrouter_vision",
                })
                if clipping_path:
                    logger.info(f"Image clipping saved: {clipping_path}")
                    meta_path = Path(inbox_path).with_name(Path(inbox_path).name + ".meta.yaml")
                    if meta_path.exists():
                        _mark_inbox_processed(str(meta_path))
                    else:
                        # Binary files can't hold a status line — create the sidecar
                        # meta so scan_inbox skips this image on subsequent runs.
                        meta_path.write_text(f"status: processed\nclipping: {clipping_path}\n")
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

        # Check if saved clipping is empty/failed
        if results and results[-1].get("status") == "success" and results[-1].get("clipping_path"):
            cpath = results[-1]["clipping_path"]
            if _is_empty_clipping(cpath):
                logger.warning(f"Clipping saved but content is empty/failed: {cpath}")
                results[-1]["status"] = "error"
                try:
                    Path(cpath).unlink(missing_ok=True)
                    logger.info(f"Deleted failed/empty clipping file: {cpath}")
                except Exception as e:
                    logger.error(f"Failed to delete empty clipping file {cpath}: {e}")

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


def _dedup_frontmatter(content: str) -> str:
    """YAML 프론트매터 중복 키 제거 (첫 번째 값 유지). LLM 출력 저장 전 적용."""
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    seen: set[str] = set()
    deduped_lines = []
    for line in parts[1].splitlines():
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*):', line)
        key = m.group(1) if m else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped_lines.append(line)
    return "---" + "\n".join(deduped_lines) + "\n---" + parts[2]


def _validate_frontmatter_all() -> int:
    """garden/ + wiki/ 전체 마크다운 파일의 중복 YAML 키를 자동 수정. 수정 건수 반환."""
    fixed = 0
    for md_file in list(PROJECT_DIR.glob("garden/**/*.md")) + list(PROJECT_DIR.glob("wiki/**/*.md")):
        try:
            content = md_file.read_text()
            deduped = _dedup_frontmatter(content)
            if deduped != content:
                md_file.write_text(deduped)
                logger.warning(f"[frontmatter] Fixed duplicate keys in {md_file.name}")
                fixed += 1
        except Exception as e:
            logger.warning(f"[frontmatter] Could not process {md_file.name}: {e}")
    return fixed





def _resolve_codex_bin() -> str | None:
    """Resolve the `codex` CLI binary, augmenting PATH for launchd daemons."""
    local_bin = str(Path.home() / ".local" / "bin")
    brew_bin = "/opt/homebrew/bin"
    app_bin = "/Applications/cmux.app/Contents/Resources/bin"
    search_path = f"{local_bin}:{brew_bin}:{app_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return shutil.which("codex", path=search_path)


def _resolve_claude_bin() -> str | None:
    """Resolve the `claude` (Claude Code) CLI binary, augmenting PATH for launchd daemons."""
    local_bin = str(Path.home() / ".local" / "bin")
    brew_bin = "/opt/homebrew/bin"
    search_path = f"{local_bin}:{brew_bin}:/usr/local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return shutil.which("claude", path=search_path)


def phase_note_make(clippings: list[dict], timeout: int = 3600) -> list[dict]:
    """Phase 4: Generate notes using the Claude Code CLI (`claude -p`).

    Claude reads the clipping + note-format.md, runs the 6 Rs pipeline, and
    WRITES the wiki/garden note files directly to disk (no fragile stdout
    parsing). Newly created files are detected by snapshotting the note dirs
    before/after the call. The prompt is passed as a positional arg (not stdin)
    so the CLI never blocks waiting for input.
    """
    logger.info(f"=== Phase 4: Note Maker ({len(clippings)} clippings) ===")

    successful_clippings = [c for c in clippings if c.get("status") == "success" and c.get("clipping_path")]
    # Filter out empty/failed clippings
    valid_clippings = [c for c in successful_clippings if not _is_empty_clipping(c["clipping_path"])]
    skipped = len(successful_clippings) - len(valid_clippings)
    if skipped:
        logger.warning(f"Skipping {skipped} empty/failed clippings")
        
    # Single source of truth for "already processed": a clipping is done iff a
    # note on disk references it via source_clipping. Notes are ground truth, so
    # deleting/quarantining a note re-enables regeneration (idempotent, re-run safe).
    note_maker_done = WORKSPACE_DIR / "note_maker_done.jsonl"
    master_done = SCRIPTS_DIR / ".note_maker_done_master.jsonl"
    from pipeline_state import processed_clipping_stems
    processed_stems = processed_clipping_stems([WIKI_DIR, GARDEN_DIR])

    valid_clippings = [c for c in valid_clippings if Path(c["clipping_path"]).stem not in processed_stems]
    logger.info(f"Skipping already processed clippings. Remaining to process: {len(valid_clippings)}")

    successful_clippings = valid_clippings
    if not successful_clippings:
        logger.warning("No successful clippings to process")
        return []

    format_file = PROJECT_DIR / ".claude" / "skills" / "note-pipeline" / "references" / "note-format.md"
    if not format_file.exists():
        logger.error(f"Format rules file not found at {format_file}")
        return [{"status": "error", "error": "format file not found"}]

    format_file_rel = format_file.relative_to(PROJECT_DIR)
    claude_bin = _resolve_claude_bin()
    if not claude_bin:
        logger.error("Claude Code CLI not found. Install it (npm i -g @anthropic-ai/claude-code) and authenticate before Phase 4.")
        return [{"status": "error", "error": "claude CLI not found"}]

    # launchd/Hermes-triggered runs often inherit a minimal PATH that cannot
    # resolve `/usr/bin/env node` from the CLI shebang. Phase 4 must pass an
    # explicit environment to the note-maker CLI, just like Phase 5 does for shell tools.
    codex_env = os.environ.copy()
    codex_path_parts = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        "/Applications/cmux.app/Contents/Resources/bin",
    ]
    existing_path = codex_env.get("PATH", "")
    codex_env["PATH"] = ":".join([*codex_path_parts, existing_path])
    codex_env.setdefault("HOME", str(Path.home()))

    logger.info(f"Note generation via Claude Code CLI: {claude_bin}")
    logger.info(f"Note-maker PATH begins with: {':'.join(codex_path_parts[:4])}")
    results = []

    def _note_snapshot() -> set:
        return set(WIKI_DIR.glob("**/*.md")) | set(GARDEN_DIR.glob("**/*.md"))

    for idx, c in enumerate(successful_clippings):
        clipping_path = c['clipping_path']
        clipping_stem = Path(clipping_path).stem
        domain_hint = c.get('domain_hint', 'general')
        logger.info(f"Processing clipping {idx+1}/{len(successful_clippings)}: {clipping_path}")

        prompt = f"""너는 note-maker 에이전트다. @.claude/agents/note-maker.md 와 규칙 파일 @{format_file_rel} 를 먼저 읽고, 그 규칙을 엄격히 준수하라.

다음 clipping 파일을 6 Rs 파이프라인(R1 Reduce → R2 Reflect → R3 Reweave → R4 Record → R5 Verify)으로 처리하라:
  @{clipping_path}

[해야 할 일]
1. clipping 원문과 note-format.md 를 읽는다.
2. 기존 wiki/·garden/ 노트를 탐색해 연결점을 찾는다(R2). 단순 요약을 넘어 숨은 의도·맥락 연결·전문가 수준의 통찰을 도출하라.
3. 노트 파일을 **직접 디스크에 생성(파일 편집 도구)** 한다:
   - wiki/ 노트 1개 (type=concept → wiki/concepts/, type=entity → wiki/entities/)
   - **wiki/ 아래 공개 하위 폴더는 concepts/와 entities/ 두 개뿐이다.** wiki/events/, wiki/tools/, wiki/people/ 같은 폴더를 만들지 말고, wiki/ 루트에 .md 파일을 직접 만들지 마라. event/procedure/analysis 성격의 machine 노트도 type=concept으로 보고 wiki/concepts/에 저장하라.
   - garden/ 노트 1~3개 (핵심 인사이트별 아토믹 노트, 제목은 완전한 문장/명제). **절대 3개를 초과하지 말 것. 억지로 쪼개지 말고 비슷한 인사이트는 통합하라.**
   - 인물·도구·조직·프레임워크가 핵심으로 등장하면 wiki/entities/ entity 노트를 추가로 생성
4. 모든 노트 프론트매터에 반드시 포함: `source_clipping: "[[{clipping_stem}]]"` 와 `status: publish`.
5. **모든 제목과 본문은 한국어로 작성.** 파일명은 영문 kebab-case 슬러그.

[중요 규칙]
- 노트를 stdout으로 출력하지 말고 반드시 프로젝트 디렉터리에 파일로 생성하라.
- 위키링크는 순수 슬러그만 사용: `[[슬러그]]`. path-prefixed(`[[concepts/슬러그]]`) 금지.
- note-format.md 의 검증 체크리스트를 통과시켜라.
- 확인을 되묻지 말고 바로 작업을 완료하라.
"""

        before = _note_snapshot()
        logger.info(f"Invoking Claude Code CLI (claude -p) for {Path(clipping_path).name}...")
        per_call_timeout = min(timeout, 1800)  # 30 min max per clipping
        poll_interval = 10  # seconds between progress logs

        try:
            # Headless, non-interactive agent run. The prompt is passed as a
            # positional arg (NOT stdin) so the CLI never blocks reading stdin.
            # --dangerously-skip-permissions lets the agent write note files to
            # the workspace without interactive approval.
            proc = subprocess.Popen(
                [
                    claude_bin,
                    "-p", prompt,
                    "--dangerously-skip-permissions",
                ],
                cwd=PROJECT_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                env=codex_env,
            )

            elapsed = 0
            while proc.poll() is None:
                if elapsed >= per_call_timeout:
                    proc.kill()
                    proc.wait(timeout=10)
                    raise subprocess.TimeoutExpired(cmd=claude_bin, timeout=per_call_timeout,
                                                    output=proc.stdout.read() if proc.stdout else "",
                                                    stderr=proc.stderr.read() if proc.stderr else "")
                time.sleep(poll_interval)
                elapsed += poll_interval
                if elapsed % 60 == 0:  # log every minute
                    logger.info(f"  Claude still running for {clipping_stem} ({elapsed}s elapsed)")

            stdout, stderr = proc.communicate(timeout=10)
            if proc.returncode != 0:
                logger.error(
                    f"claude -p exited {proc.returncode} for {clipping_stem}: "
                    f"{(stderr or '')[:300]}"
                )
        except subprocess.TimeoutExpired:
            logger.error(f"claude -p timed out after {per_call_timeout}s for {clipping_stem}")
            results.append({"clipping_path": str(clipping_path), "status": "error", "error": "timeout"})
            continue
        except Exception as e:
            logger.error(f"claude -p crashed for {clipping_stem}: {e}")
            results.append({"clipping_path": str(clipping_path), "status": "error", "error": str(e)})
            continue

        # Detect newly created note files; sanitize duplicate frontmatter keys.
        new_paths = sorted(_note_snapshot() - before)
        saved_files = []
        for p in new_paths:
            try:
                p.write_text(_dedup_frontmatter(p.read_text()))
            except Exception:
                pass
            saved_files.append(str(p.relative_to(PROJECT_DIR)))

        if saved_files:
            for f in saved_files:
                logger.info(f"  -> Saved {f}")
            results.append({
                "clipping_path": str(clipping_path),
                "generated_files": saved_files,
                "status": "success",
            })
            for done_file in (note_maker_done, master_done):
                try:
                    with open(done_file, "a") as fh:
                        fh.write(json.dumps({"clipping_path": str(clipping_path), "status": "success", "generated_files": saved_files}, ensure_ascii=False) + "\n")
                except Exception as e:
                    logger.warning(f"Could not write done log {done_file}: {e}")
        else:
            logger.warning(f"No new notes created by Codex for {clipping_stem}")
            results.append({
                "clipping_path": str(clipping_path),
                "generated_files": [],
                "status": "parse_error",
            })

    logger.info(f"Note maker completed: processed {len(results)} clippings.")
    return results


def _local_head(cwd: Path = PROJECT_DIR) -> str:
    r = run_cmd(["git", "rev-parse", "HEAD"], cwd=cwd, check=False)
    return (r.stdout or "").strip()


def _remote_head(branch: str = "main", cwd: Path = PROJECT_DIR) -> str:
    r = run_cmd(["git", "ls-remote", "origin", f"refs/heads/{branch}"], cwd=cwd, check=False)
    out = (r.stdout or "").strip()
    return out.split()[0] if out else ""


def _confirm_remote_synced(branch: str = "main", retries: int = 6, delay: int = 5) -> bool:
    """Return True once origin/<branch> == local HEAD.

    The live site is built by GitHub Actions checking out 0004_Wikis main, so a
    deploy triggered before the push has propagated builds STALE content. Confirm
    the remote is in sync before triggering deploy to close that race."""
    local = _local_head()
    if not local:
        return False
    for _ in range(retries):
        if _remote_head(branch) == local:
            return True
        time.sleep(delay)
    return False


def _verify_live_reflects(path: str, needle: str, retries: int = 6, delay: int = 15) -> bool:
    """Best-effort: poll the live site until `needle` appears at SITE_BASE_URL/path."""
    url = f"{SITE_BASE_URL}/{path.lstrip('/')}"
    for _ in range(retries):
        r = run_cmd(["curl", "-s", "-H", "Cache-Control: no-cache", url], check=False)
        if needle in (r.stdout or ""):
            return True
        time.sleep(delay)
    return False


def phase_publish() -> bool:
    """Phase 5: Quartz build + deploy."""
    logger.info("=== Phase 5: Publisher ===")

    # Run audit check
    logger.info("Running content audit...")
    try:
        python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"
        subprocess.run([python_bin, str(SCRIPTS_DIR / "audit_content.py")], check=True)
        logger.info("Content audit passed.")
    except subprocess.CalledProcessError:
        logger.error("Content audit failed! Aborting publish.")
        return False

    # Pre-flight: run link corrector and backlinks generator
    try:
        logger.info("Running link corrector and backlinks indexer...")
        python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"
        run_cmd([python_bin, str(SCRIPTS_DIR / "link_corrector.py")], check=True)
    except Exception as e:
        logger.warning(f"Link corrector failed (non-fatal): {e}")

    # Run clipping-note graph links correction
    try:
        logger.info("Running fix_graph_links.py...")
        python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"
        run_cmd([python_bin, str(SCRIPTS_DIR / "maintenance" / "fix_graph_links.py")], check=True)
    except Exception as e:
        logger.warning(f"fix_graph_links failed (non-fatal): {e}")

    # Unified normalization (titles + dates + wikilinks) via normalize.py.
    # Replaces the deprecated fix_dates.py, which force-injected filename dates
    # into frontmatter (the 2066-year bug). normalize.py keeps frontmatter dates
    # authoritative, cleans titles, and writes through integrity guards.
    try:
        logger.info("Running normalize.py (titles + dates + wikilinks)...")
        python_bin = VENV_PYTHON if os.path.exists(VENV_PYTHON) else "python3"
        run_cmd([python_bin, str(PROJECT_DIR / "scripts" / "normalize.py"), "--apply", "--no-moc"], check=True)
    except Exception as e:
        logger.warning(f"normalize.py failed (non-fatal): {e}")

    # Schema-validation gate: quarantine structurally broken notes (no frontmatter
    # / empty title) so they are excluded from publish. Missing provenance fields
    # are logged as warnings, not quarantined (avoids nuking the legacy vault).
    try:
        from validate_schema import validate_and_quarantine, stage_gate
        vres = validate_and_quarantine([WIKI_DIR, GARDEN_DIR], apply=True)
        stage_gate(
            "schema-validation",
            True,  # gate is non-fatal: broken notes are quarantined, valid ones proceed
            f"valid={vres['valid']} quarantined={len(vres['quarantined'])} warned={len(vres['warned'])}",
        )
        if vres["quarantined"]:
            logger.warning(f"Quarantined {len(vres['quarantined'])} broken note(s): "
                           f"{[p for p, _ in vres['quarantined'][:5]]}")
    except Exception as e:
        logger.warning(f"schema validation gate failed (non-fatal): {e}")

    # Pre-flight: fix any duplicate YAML frontmatter keys before sync
    fixed = _validate_frontmatter_all()
    if fixed:
        logger.warning(f"Pre-publish: fixed duplicate frontmatter keys in {fixed} file(s)")

    # Generate domain MOCs
    try:
        from generate_moc import generate_moc
        moc_files = generate_moc(PROJECT_DIR)
        logger.info(f"MOC generated: {len(moc_files)} files")
    except Exception as e:
        logger.warning(f"MOC generation failed (non-fatal): {e}")

    # Step 0: Validate generated notes
    logger.info("Validating generated notes...")
    validate_result = run_cmd(
        ["python3", str(SCRIPTS_DIR / "validate_notes.py"), "wiki/", "garden/"],
        check=False
    )
    if validate_result.returncode != 0:
        logger.warning(f"Note validation found issues:\n{validate_result.stdout[:500]}")
    else:
        logger.info("Note validation passed")

    # Step 1: Sync content
    logger.info("Syncing content...")
    sync_result = run_cmd(["bash", str(SCRIPTS_DIR / "sync_content.sh")], check=False)
    if sync_result.returncode != 0:
        logger.warning(f"Content sync had issues (non-fatal): {sync_result.stderr[:200]}")

    # Step 2: Check links
    logger.info("Checking links...")
    run_cmd(["python3", str(SCRIPTS_DIR / "check_links.py")], check=False)

    # Step 3: Push 0004_Wikis content to GitHub (GitHub Actions pulls from this repo)
    logger.info("Pushing content to GitHub...")
    run_cmd(["git", "add", "-A", "wiki/", "garden/", "clippings/", "scripts/", "index.md"],
            cwd=PROJECT_DIR, check=False)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    commit_result = run_cmd(
        ["git", "commit", "-m", f"content: pipeline auto-publish {ts}"],
        cwd=PROJECT_DIR, check=False,
    )
    if commit_result.returncode != 0:
        logger.info("Nothing new to commit for 0004_Wikis")
    # Always push — GitHub Actions reads from the remote, not local commits
    push_result = run_cmd(["git", "push", "origin", "main"], cwd=PROJECT_DIR, check=False)
    if push_result.returncode != 0:
        logger.warning(f"git push failed (non-fatal): {push_result.stderr[:200]}")

    # Race guard: the deploy (triggered by `quartz sync` below) checks out
    # 0004_Wikis main. Confirm origin/main == local HEAD BEFORE deploying so the
    # build never picks up stale content.
    if _confirm_remote_synced("main"):
        logger.info("[gate:remote-sync] PASS — origin/main matches local HEAD before deploy")
    else:
        logger.warning("[gate:remote-sync] WARN — origin/main not confirmed in sync; "
                       "deploy may build stale content (will still proceed)")

    # Step 4: Build and deploy
    logger.info("Building and deploying Quartz site...")
    # Daemon PATH omits /opt/homebrew/bin — inject it explicitly so npx can find node
    brew_bin = "/opt/homebrew/bin"
    node_env = os.environ.copy()
    node_env["PATH"] = f"{brew_bin}:{node_env.get('PATH', '/usr/bin:/bin')}"
    npx = shutil.which("npx", path=node_env["PATH"]) or f"{brew_bin}/npx"
    deploy_result = run_cmd(
        [npx, "quartz", "sync"],
        cwd=QUARTZ_DIR,
        check=False,
        env=node_env,
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


def phase_report(ingest_results: list, note_results: list, publish_success, diagnostics_report: str = "",
                 run_duration: float = 0.0):
    """Phase 6: Generate summary report and send Telegram notification."""
    logger.info("=== Phase 6: Report ===")

    successful_clips = len([r for r in ingest_results if r.get("status") == "success"])
    failed_clips = len([r for r in ingest_results if r.get("status") == "error"])
    notes_created = len(note_results)
    notes_ok = len([r for r in note_results if r.get("status") == "success"])
    notes_fail = len([r for r in note_results if r.get("status") in ("error", "parse_error", "timeout")])
    _, publish_text = _publish_label(publish_success)

    # Overall health: a run is failed if publish failed, or notes were attempted
    # but none succeeded. This drives the status marker + failure alert.
    pub_failed = publish_success is False
    notegen_failed = notes_created > 0 and notes_ok == 0
    ok = not (pub_failed or notegen_failed)
    status_marker = "✅ 성공" if ok else "❌ 실패"
    duration_txt = f"{run_duration:.0f}s" if run_duration else "n/a"

    report = f"""
파이프라인 실행 결과 [{status_marker}] — {datetime.now().strftime('%Y-%m-%d %H:%M')} · 소요 {duration_txt}
{'=' * 50}
- 수신: {len(ingest_results)}개 항목
- 클리핑: {successful_clips}개 완료 / {failed_clips}개 실패
- 노트 생성: {notes_created}개 (성공 {notes_ok} / 실패 {notes_fail})
- 퍼블리시: {publish_text}
- 사이트: {SITE_BASE_URL}/

{diagnostics_report}
{'=' * 50}
"""
    # Stage gate marker (structured, greppable) + explicit failure log.
    logger.info(f"[gate:pipeline] {'PASS' if ok else 'FAIL'} — "
                f"notes {notes_ok}/{notes_created} ok, publish={publish_text}, {duration_txt}")
    if not ok:
        logger.error(f"[gate:pipeline] FAIL — pub_failed={pub_failed} notegen_failed={notegen_failed}")
    print(report)
    logger.info(report)

    # Send Telegram notification
    notify_telegram(ingest_results, note_results, publish_success)

    # Rotate workspace
    workspace_prev = PROJECT_DIR / "_workspace_prev"
    if workspace_prev.exists():
        shutil.rmtree(workspace_prev)
    WORKSPACE_DIR.rename(workspace_prev)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # Write summary to new workspace
    with open(WORKSPACE_DIR / "last_run_summary.md", "w") as f:
        f.write(report)


def main():
    parser = argparse.ArgumentParser(description="Wiki Pipeline Orchestrator")
    parser.add_argument("--phase", type=str, default="3-7",
                        help="Phase range to run (e.g. 3-7, 4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without executing")
    parser.add_argument("--timeout", type=int, default=3600,
                        help="Timeout in seconds for Phase 4 Codex CLI (default: 3600)")
    args = parser.parse_args()

    lock_fd = None
    if not args.dry_run:
        import fcntl
        try:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = open(LOCK_FILE, "a")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.error("Pipeline is already running (lock file active). Exiting.")
            sys.exit(1)

    try:
        run_start = time.time()
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
        publish_success = None  # None=skipped, True=success, False=failed

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
                print(f"[DRY RUN] Would invoke Codex CLI for note generation")
            else:
                # Use ingestor_done.jsonl if available
                done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
                if done_file.exists():
                    with open(done_file) as f:
                        clippings = [json.loads(line.strip()) for line in f]
                else:
                    clippings = list(ingest_results)
                    
                # Scan clippings directory and merge any files not in current list
                existing_paths = {Path(c["clipping_path"]).resolve() for c in clippings if c.get("clipping_path")}
                CLIPPINGS_DIR.mkdir(parents=True, exist_ok=True)
                for f in CLIPPINGS_DIR.glob("*.md"):
                    if f.resolve() not in existing_paths:
                        clippings.append({
                            "clipping_path": str(f.relative_to(PROJECT_DIR)),
                            "status": "success",
                            "domain_hint": "general"
                        })

                if clippings:
                    note_results = phase_note_make(clippings, timeout=args.timeout)
                else:
                    logger.warning("No clippings to process for note generation")

        # Skip Phase 5 if no notes were created AND Phase 4 was also requested AND no git changes are present
        if 5 in phases and 4 in phases and not note_results:
            # Check if there are any pending git changes in wiki, garden, or clippings
            git_status = subprocess.run(
                ["git", "status", "--porcelain", "wiki/", "garden/", "clippings/"],
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                errors="replace"
            )
            has_git_changes = bool(git_status.stdout.strip())
            if not has_git_changes:
                if not args.dry_run:
                    logger.info("No notes created and no local git changes in wiki/garden/clippings — skipping publish (nothing new to deploy)")
                    publish_success = None  # skipped, not failed
                    phases.discard(5)
            else:
                logger.info("No new notes generated, but local git changes detected — proceeding to publish")

        # Phase 5: Publisher
        if 5 in phases:
            if args.dry_run:
                print("[DRY RUN] Would run Quartz build + deploy")
            else:
                try:
                    result = phase_publish()
                    publish_success = True if result else False
                except Exception as e:
                    logger.error(f"phase_publish crashed: {e}")
                    publish_success = False

        diagnostics_report = ""
        # Phase 7: Diagnostics & Auto-Healing
        if 7 in phases:
            if args.dry_run:
                print("[DRY RUN] Would run diagnostics and auto-healing")
            else:
                try:
                    logger.info("=== Phase 7: Diagnostics ===")
                    sys.path.append(str(WORKSPACE_DIR / "scripts"))
                    import diagnostics
                    diagnostics_report = diagnostics.run_all_diagnostics(auto_heal=True)
                except Exception as e:
                    logger.error(f"Phase 7 crashed: {e}")
                    diagnostics_report = f"Diagnostics failed: {e}"

        # Phase 6: Report — always runs even if earlier phases crashed
        if 6 in phases and not args.dry_run:
            try:
                phase_report(ingest_results, note_results, publish_success, diagnostics_report,
                             run_duration=time.time() - run_start)
            except Exception as e:
                logger.error(f"phase_report crashed: {e}")

        logger.info("Pipeline complete")

    finally:
        if lock_fd:
            import fcntl
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
            try:
                LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    main()
