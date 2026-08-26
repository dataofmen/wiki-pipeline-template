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
    successful_clips = len([r for r in ingest_results if r.get("status") == "success"])
    notes_created = len(note_results)
    
    # A run is a no-op if:
    # 1. No items were successfully ingested AND no notes were created
    # 2. AND the publish step didn't actually fail (it either succeeded or was skipped)
    # This prevents "0 items processed" noise while still alerting on actual publish failures.
    if successful_clips == 0 and notes_created == 0 and publish_success is not False:
        logger.info("No-op pipeline run (no new clips or notes) — skipping Telegram notification")
        return True

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

    # VERIFY 게이트에서 걸러진 클리핑을 보고한다(조용히 사라지면 재추출 기회를 놓친다).
    gated = [r for r in ingest_results if r.get("status") == "fetch_failed"]
    if gated:
        lines.append("")
        lines.append(f"🚫 *본문 검증 실패 {len(gated)}건* — 재추출 필요")
        for r in gated[:5]:
            lines.append(f"  • {r.get('source_url', '')[:70]}")

    # 트리아지 미확인 보고 — classification 없는 노트가 있으면 R0 를 건너뛴 것이다.
    tri_missing = sum(len((r.get("triage") or {}).get("missing", [])) for r in note_results)
    tri_ad = sum(len((r.get("triage") or {}).get("ad", [])) for r in note_results)
    if tri_missing or tri_ad:
        lines.append("")
        if tri_missing:
            lines.append(f"⚠️ *트리아지 미확인 {tri_missing}개* — classification 없는 노트")
        if tri_ad:
            lines.append(f"📢 *광고 분류 {tri_ad}개* — 퍼블리시 제외 확인 필요")

    # 링크 건강: 기준선보다 악화된 경우에만 보고한다. 정상일 때 조용해야 신호가 산다.
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import check_links as _cl
        _b, _a = _cl.check_links(PROJECT_DIR)
        m = _cl.summarize(_b, _a)
        base = _cl.load_baseline() or {}
        d_act = m["actionable"] - base.get("actionable", 0)
        d_amb = m["ambiguous_body"] - base.get("ambiguous_body", 0)
        if m["actionable"] > 0 or d_act > 0 or d_amb > 0:
            lines.append("")
            lines.append("🔗 *링크 건강 악화*")
            if m["actionable"] > 0:
                lines.append(f"  • 조치 필요(본문·대상 소실): {m['actionable']}건 ({d_act:+d})")
            if d_amb > 0:
                lines.append(f"  • 중복 슬러그: {m['ambiguous_body']}건 ({d_amb:+d})")
            lines.append("  _`python3 scripts/check_links.py --list` 로 확인_")
    except Exception as e:
        logger.warning(f"링크 건강 조회 실패: {e}")

    # Tier-0 알림 큐: 콘텐츠 안의 시한성 정보
    try:
        from alerts import format_for_notification
        alert_block = format_for_notification()
    except Exception as e:
        logger.warning(f"알림 큐 조회 실패: {e}")
        alert_block = ""
    if alert_block:
        lines += ["", alert_block]

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
    from urllib.parse import urlparse, urlencode, parse_qsl, unquote
    url = url.strip().strip('"').strip("'")
    # Percent-decode so encoded (inbox) and decoded (clipping) forms of the same
    # URL compare equal — otherwise the orphan check re-fetches forever.
    url = unquote(url)
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
    """클리핑에 실제 본문이 없는지 판정. content_validation 이 SSOT 다.

    FAILED 만 True 로 본다. SUSPECT(짧지만 정상일 수 있는 단문)는 통과시키고
    노트 생성 프롬프트에서 보수적으로 다루게 한다.
    """
    from content_validation import validate_clipping_file
    try:
        return validate_clipping_file(path).failed
    except Exception:
        return True


def _clipping_validation(path: str):
    """클리핑 검증 결과를 그대로 반환(경고 문구를 프롬프트에 실을 때 사용)."""
    from content_validation import validate_clipping_file, ValidationResult, FAILED, EMPTY
    try:
        return validate_clipping_file(path)
    except Exception as e:
        return ValidationResult(FAILED, EMPTY, f"검증 실패: {e}", 0, [])


def _clipping_marked_failed(path: str) -> bool:
    """프론트매터가 이미 `status: fetch_failed` 로 표시된 클리핑인지."""
    try:
        head = Path(path).read_text(errors="ignore")[:1200]
        return bool(re.search(r"^status:\s*[\"']?fetch_failed", head, re.MULTILINE))
    except Exception:
        return False


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
                    # Skip unless meta.yaml says pending/processing (or has no status).
                    # e.g. "processed" and "archived" (backlog freeze) must not re-ingest.
                    if meta_status not in ("", "pending", "processing"):
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
                # PDFs carry no inline status — consult the sidecar meta.yaml
                # so processed/archived PDFs aren't re-ingested every run.
                meta_path = f.parent / f"{f.name}.meta.yaml"
                if meta_path.exists():
                    meta_content = meta_path.read_text(errors="ignore")
                    if "status: pending" not in meta_content and "status: processing" not in meta_content:
                        continue
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

                # Dedup: 로컬 파일은 URL 이 없어 URL dedup 이 통하지 않는다. 본문
                # 지문으로 확인한다. (실측: 동일 .txt 가 08-25/08-26/08-27 3회 재클립)
                from fetcher_bridge import find_existing_clipping
                dup = find_existing_clipping(source_url or "", CLIPPINGS_DIR, content=content)
                if dup:
                    logger.info(f"Dedup: 동일 본문 클리핑 재사용 {dup.name} ({inbox_path})")
                    meta_path = Path(inbox_path).with_name(Path(inbox_path).name + ".meta.yaml")
                    _mark_inbox_processed(str(meta_path) if meta_path.exists() else inbox_path)
                    results.append({
                        "source_url": source_url or inbox_path,
                        "clipping_path": str(dup),
                        "status": "duplicate",
                        "domain_hint": domain_hint,
                    })
                    continue

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
                        # Binary files can't hold a status line — create the sidecar
                        # meta so scan_inbox skips this PDF on subsequent runs.
                        meta_path.write_text(f"status: processed\nclipping: {clipping_path}\n")

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
    override = os.environ.get("PIPELINE_CLAUDE_BIN")
    if override:
        return override if Path(override).exists() else None
    local_bin = str(Path.home() / ".local" / "bin")
    brew_bin = "/opt/homebrew/bin"
    search_path = f"{local_bin}:{brew_bin}:/usr/local/bin:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return shutil.which("claude", path=search_path)


def _is_claude_capacity_error(detail: str) -> bool:
    """Return True for Claude CLI/runtime failures that code repair cannot fix."""
    text = (detail or "").lower()
    capacity_markers = [
        "weekly limit",
        "usage limit",
        "rate limit",
        "quota",
        "resets ",
        "not authenticated",
        "authentication",
        "login",
        "oauth session expired",
        "sessionend hook",
        "hook cancelled",
        "hook failed",
    ]
    return any(marker in text for marker in capacity_markers)


def _cli_excerpt(stdout: str = "", stderr: str = "", limit: int = 500) -> str:
    """Compact CLI output for logs/result metadata without flooding pipeline.log."""
    detail = "\n".join(part.strip() for part in (stderr, stdout) if part and part.strip())
    return detail[:limit]


def _audit_triage(clipping_stem: str, saved_files: list[str]) -> dict:
    """생성된 노트가 R0 트리아지를 실제로 거쳤는지 검사한다.

    CLASSIFY 는 프롬프트 지시문이므로 LLM 이 조용히 건너뛸 수 있다. VERIFY 는 코드가
    파일에 `status: fetch_failed` 를 쓰고 코드가 검사하지만, CLASSIFY 에는 그런 흔적이
    없었다 — 규칙을 넣은 뒤에도 `classification` 필드를 가진 노트가 볼트 전체에 0개였다.
    `classification` 을 필수 필드로 두고 생성 직후 검사해, 건너뜀을 **관측 가능**하게 만든다.

    반환: {"classified": n, "missing": [파일...], "ad": [파일...]}
    """
    classified, missing, ad = 0, [], []
    for rel in saved_files:
        path = PROJECT_DIR / rel
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2500]
        except Exception:
            continue
        m = re.search(r"^classification:\s*[\"']?(\w+)", head, re.MULTILINE)
        if not m:
            missing.append(rel)
            continue
        classified += 1
        if m.group(1).lower() == "ad":
            ad.append(rel)
    if missing:
        logger.warning(
            f"[{clipping_stem}] 트리아지 미확인 — classification 없는 노트 "
            f"{len(missing)}/{len(saved_files)}개: {missing[:3]}"
        )
    if ad:
        logger.warning(f"[{clipping_stem}] 광고로 분류된 노트 {len(ad)}개 (퍼블리시 제외 대상): {ad}")
    return {"classified": classified, "missing": missing, "ad": ad}


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
    # VERIFY 게이트 — 본문이 아닌 클리핑은 노트 생성에서 제외한다. 프론트매터가
    # 이미 fetch_failed 로 표시된 것과, 지금 검증에서 FAILED 로 판정된 것 모두 제외.
    # (에러 페이지 1건이 환각 노트 5개를 만든 사고를 막는 지점)
    valid_clippings = []
    for c in successful_clippings:
        cpath = c["clipping_path"]
        if _clipping_marked_failed(cpath):
            logger.warning(f"Skip (status: fetch_failed): {Path(cpath).name}")
            continue
        v = _clipping_validation(cpath)
        if v.failed:
            logger.warning(f"Skip (검증 실패 {v.code}): {Path(cpath).name} — {v.reason}")
            continue
        if v.verdict != "ok":
            logger.warning(f"주의 ({v.code}): {Path(cpath).name} — {v.reason}")
        c["validation"] = {"verdict": v.verdict, "code": v.code, "reason": v.reason,
                           "word_count": v.word_count}
        valid_clippings.append(c)
    skipped = len(successful_clippings) - len(valid_clippings)
    if skipped:
        logger.warning(f"Skipping {skipped} invalid clippings (VERIFY 게이트)")
        
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

    # Claude Code keeps shared state under ~/.claude; concurrent headless runs
    # have been observed to exit early with no stderr. Keep the default serial,
    # while still allowing an explicit opt-in for backlog jobs.
    max_workers = max(1, int(os.environ.get("PIPELINE_NOTE_WORKERS", "1")))
    logger.info(f"Note generation via Claude Code CLI: {claude_bin} (workers: {max_workers})")
    logger.info(f"Note-maker PATH begins with: {':'.join(codex_path_parts[:4])}")
    phase_start = time.time()

    def _attribute_new_notes(clipping_stem: str) -> list[str]:
        """Find the notes generated for this clipping. Under parallel workers a
        global before/after snapshot would swallow sibling workers' files, so
        ownership is determined by the source_clipping frontmatter every note
        must carry (stems are unique per clipping)."""
        from pipeline_state import SOURCE_CLIPPING_RE
        saved = []
        for root in (WIKI_DIR, GARDEN_DIR):
            for p in root.glob("**/*.md"):
                try:
                    if p.stat().st_mtime < phase_start - 1:
                        continue
                    m = SOURCE_CLIPPING_RE.search(p.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
                if m and re.sub(r"\.md$", "", m.group(1).strip()) == clipping_stem:
                    try:
                        p.write_text(_dedup_frontmatter(p.read_text()))
                    except Exception:
                        pass
                    saved.append(str(p.relative_to(PROJECT_DIR)))
        return sorted(saved)

    def _process_one(c: dict) -> dict:
        clipping_path = c['clipping_path']
        clipping_stem = Path(clipping_path).stem
        logger.info(f"[{clipping_stem}] Processing clipping: {clipping_path}")

        # 검증 경고가 있으면 프롬프트에 명시해 환각을 억제한다.
        v = c.get("validation") or {}
        warning_block = ""
        if v.get("code") == "low_value":
            warning_block = f"""
[가치 경고 — 트리아지를 특히 엄격히 하라]
기계적 검사에서 광고·유인 지표가 감지됐다: {v.get('reason')}
이 클리핑은 본문 자체는 정상이지만 **광고·리딩방 유인·낚시**일 가능성이 있다.
- 재현 가능한 절차·검증 가능한 데이터가 없고 성과 자랑과 유인만 있으면 **노트를 만들지 마라.**
- 옮길 알맹이가 실제로 있으면 garden 1개로 제한하고 `classification: ad` 를 남겨라.
- 기계 신호는 판정이 아니다. 본문을 읽고 네가 판단하라.
"""
        elif v.get("verdict") and v["verdict"] != "ok":
            warning_block = f"""
[본문 신뢰도 경고 — 반드시 준수]
이 클리핑은 검증에서 `{v.get('code')}` 로 표시됐다: {v.get('reason')}
본문이 {v.get('word_count')}단어뿐이다. 따라서:
- **원문에 명시된 내용만 쓴다.** URL 슬러그·제목·도메인명으로 내용을 추론해 채우지 마라.
- 노트 개수를 최소로 한다 (wiki 1개, garden 최대 1개).
- 근거가 부족해 노트를 만들 수 없다고 판단되면, 노트를 만들지 말고 그 사유를 출력하라.
"""

        prompt = f"""너는 note-maker 에이전트다. @.claude/agents/note-maker.md 와 규칙 파일 @{format_file_rel} 를 먼저 읽고, 그 규칙을 엄격히 준수하라.

다음 clipping 파일을 6 Rs 파이프라인(R1 Reduce → R2 Reflect → R3 Reweave → R4 Record → R5 Verify)으로 처리하라:
  @{clipping_path}

[0단계 — 트리아지 (R1 보다 먼저)]
@.claude/skills/note-pipeline/references/content-triage.md 를 읽고 판정하라.
- 프론트매터가 `status: fetch_failed` 이거나 본문이 에러/로그인/CAPTCHA 화면이면 → 노트를 만들지 말고 사유를 출력하고 종료.
- 광고·스팸(자기홍보가 핵심 의도, 수익 자랑으로 신뢰를 만들고 방법은 없음, 링크 덤프)이면 → 노트를 만들지 말거나, 옮길 알맹이가 있을 때만 garden 1개로 제한하고 `classification: ad` 를 남긴다.
- 유용하면 → `classification: useful` + `classification_reason` 한 줄을 모든 노트 프론트매터에 넣고 진행.

**`classification` 은 필수 필드다.** 생성한 모든 노트에 반드시 넣어라. 파이프라인이
생성 직후 이 필드를 검사하며, 없으면 트리아지를 건너뛴 것으로 기록된다.
{warning_block}
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
6. **노트 파일명을 클리핑 파일명(`{clipping_stem}`)과 같게 만들지 마라.** 슬러그가
   충돌하면 Quartz 의 shortest 해상도가 `[[슬러그]]` 를 노트와 클리핑 중 어디로
   보낼지 결정할 수 없다. 노트 파일명은 **내용을 서술하는 슬러그**로 지어라
   (예: 클리핑이 `20260613-ep98-ko-transcript` 면 노트는
   `20260613-context-window-as-attention-budget` 처럼).

[중요 규칙]
- 노트를 stdout으로 출력하지 말고 반드시 프로젝트 디렉터리에 파일로 생성하라.
- 위키링크는 순수 슬러그만 사용: `[[슬러그]]`. path-prefixed(`[[concepts/슬러그]]`) 금지.
- note-format.md 의 검증 체크리스트를 통과시켜라.
- 확인을 되묻지 말고 바로 작업을 완료하라.
- **원문에 없는 사실·수치·인용을 만들지 마라.** 근거는 반드시 클리핑 본문에서 온 것이어야 한다.
  (사고 사례: Medium 500 에러 페이지에서 URL 슬러그만 보고 개념 노트 5개가 환각 생성됐다)
- 원문에 명시된 시한성 정보(만료일·마감일·보안 이슈)가 있으면
  `python3 scripts/alerts.py add "<요약>" --category <security|account_expiry|project_risk|deadline> --source {clipping_stem}` 를 실행하라. 날짜를 추측하지 마라.
"""

        logger.info(f"[{clipping_stem}] Invoking Claude Code CLI (claude -p)...")
        per_call_timeout = min(timeout, 1800)  # 30 min max per clipping
        poll_interval = 10  # seconds between progress logs
        cli_returncode = 0
        cli_detail = ""

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
                    logger.info(f"[{clipping_stem}] Claude still running ({elapsed}s elapsed)")

            stdout, stderr = proc.communicate(timeout=10)
            cli_returncode = proc.returncode
            cli_detail = _cli_excerpt(stdout, stderr)
            if proc.returncode != 0:
                logger.error(
                    f"[{clipping_stem}] claude -p exited {proc.returncode}: "
                    f"{cli_detail[:500]}"
                )
                if _is_claude_capacity_error(cli_detail):
                    return {
                        "clipping_path": str(clipping_path),
                        "generated_files": [],
                        "status": "deferred",
                        "error": cli_detail[:500],
                    }
        except subprocess.TimeoutExpired:
            logger.error(f"[{clipping_stem}] claude -p timed out after {per_call_timeout}s")
            return {"clipping_path": str(clipping_path), "status": "error", "error": "timeout"}
        except Exception as e:
            logger.error(f"[{clipping_stem}] claude -p crashed: {e}")
            return {"clipping_path": str(clipping_path), "status": "error", "error": str(e)}

        saved_files = _attribute_new_notes(clipping_stem)
        if saved_files:
            for f in saved_files:
                logger.info(f"[{clipping_stem}] -> Saved {f}")
            triage = _audit_triage(clipping_stem, saved_files)
            return {
                "clipping_path": str(clipping_path),
                "generated_files": saved_files,
                "status": "success",
                "triage": triage,
            }
        if cli_detail:
            logger.info(f"[{clipping_stem}] claude output without generated notes: {cli_detail}")
        if cli_returncode != 0:
            logger.warning(f"[{clipping_stem}] No generated notes after claude failure; marking parse_error")
            return {
                "clipping_path": str(clipping_path),
                "generated_files": [],
                "status": "parse_error",
                "error": cli_detail,
            }
        logger.warning(f"[{clipping_stem}] No new notes with matching source_clipping found; marking skipped")
        return {
            "clipping_path": str(clipping_path),
            "generated_files": [],
            "status": "skipped",
            "reason": "no_notes_generated",
            "output": cli_detail,
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_one, c): c for c in successful_clippings}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:
                c = futures[fut]
                logger.error(f"Note worker crashed for {c.get('clipping_path')}: {e}")
                r = {"clipping_path": str(c.get("clipping_path", "")), "status": "error", "error": str(e)}
            results.append(r)
            if r.get("status") == "success":
                # Done logs are written from the main thread only, so concurrent
                # workers never interleave partial lines in the JSONL files.
                for done_file in (note_maker_done, master_done):
                    try:
                        with open(done_file, "a") as fh:
                            fh.write(json.dumps({"clipping_path": r["clipping_path"], "status": "success", "generated_files": r["generated_files"]}, ensure_ascii=False) + "\n")
                    except Exception as e:
                        logger.warning(f"Could not write done log {done_file}: {e}")

    logger.info(f"Note maker completed: processed {len(results)} clippings ({max_workers} workers).")
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
    notes_deferred = len([r for r in note_results if r.get("status") == "deferred"])
    notes_skipped = len([r for r in note_results if r.get("status") == "skipped"])
    _, publish_text = _publish_label(publish_success)

    # Overall health: a run is failed if publish failed, or notes were attempted
    # but none succeeded for a repairable reason. Claude account/capacity limits
    # are deferred work, and explicit/no-note triage is skipped work; neither
    # should trigger Telegram-agent self-repair.
    pub_failed = publish_success is False
    notegen_failed = (
        notes_created > 0
        and notes_ok == 0
        and (notes_deferred + notes_skipped) != notes_created
    )
    ok = not (pub_failed or notegen_failed)
    status_marker = "✅ 성공" if ok else "❌ 실패"
    duration_txt = f"{run_duration:.0f}s" if run_duration else "n/a"

    report = f"""
파이프라인 실행 결과 [{status_marker}] — {datetime.now().strftime('%Y-%m-%d %H:%M')} · 소요 {duration_txt}
{'=' * 50}
- 수신: {len(ingest_results)}개 항목
- 클리핑: {successful_clips}개 완료 / {failed_clips}개 실패
- 노트 생성: {notes_created}개 (성공 {notes_ok} / 실패 {notes_fail} / 지연 {notes_deferred} / 스킵 {notes_skipped})
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
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(LOCK_FILE, "a")
        # Wait for the lock instead of hard-failing: rapid-fire Telegram events
        # legitimately start overlapping runs, and an immediate exit(1) here used
        # to be misread as a pipeline bug (triggering pointless self-repair).
        lock_wait = int(os.environ.get("PIPELINE_LOCK_WAIT", "1800"))
        deadline = time.time() + lock_wait
        waiting_logged = False
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    logger.error(f"Pipeline lock still held after {lock_wait}s — giving up (exit 3).")
                    sys.exit(3)
                if not waiting_logged:
                    logger.info(f"Pipeline already running — waiting up to {lock_wait}s for the lock...")
                    waiting_logged = True
                time.sleep(5)
        if waiting_logged:
            logger.info("Pipeline lock acquired after waiting.")

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
                if 3 in phases:
                    # A Telegram-triggered 3-7 run should process only clippings
                    # created in this run. If Phase 3 found nothing (often because
                    # the URL was already clipped), do not drag stale backlog into
                    # the run and mark the Telegram workflow as failed.
                    clippings = list(ingest_results)
                    if not clippings:
                        logger.info("Phase 3 produced no new clippings; skipping Phase 4 backlog scan for this run")
                else:
                    # Explicit Phase 4/backlog runs may process all unprocessed
                    # clippings on disk.
                    done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
                    if done_file.exists():
                        with open(done_file) as f:
                            clippings = [json.loads(line.strip()) for line in f]
                    else:
                        clippings = []

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
