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
from logging.handlers import RotatingFileHandler
import os
import shutil
import subprocess
import sys
import re
import time
from datetime import datetime
from pathlib import Path
from openai import OpenAI

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


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a command and log output."""
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd or PROJECT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
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


def _get_already_clipped_urls() -> set[str]:
    """Return normalized set of URLs already present in clippings/."""
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
                    raw_path = entry.get("inbox_path") or entry.get("path") or entry.get("file_path")
                    if not raw_path: continue
                    inbox_path = PROJECT_DIR / raw_path
                    if inbox_path.exists():
                        source_url = entry.get("source_url", "")
                        if source_url and _normalize_url(source_url) in already_clipped:
                            logger.info(f"Skipping already-clipped URL: {source_url}")
                            _mark_inbox_processed(str(inbox_path))
                            continue
                        entry["inbox_path_resolved"] = str(inbox_path)
                        entry["status"] = "processing"
                        pending.append(entry)
        logger.info(f"Found {len(pending)} pending items from kiwi")

    # Also scan inbox/ for any unprocessed files
    for subdir in ["urls", "threads", "pdfs", "texts"]:
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
                    entry_type = "thread" if subdir == "threads" else ("pdf" if subdir == "pdfs" else ("text" if subdir == "texts" else "url"))
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
                    if meta_path.exists():
                        for line in meta_path.read_text(errors="ignore").split("\n"):
                            if line.startswith("source_url:"):
                                source_url = line.split("source_url:", 1)[1].strip()
                            if line.startswith("title:"):
                                title = line.split("title:", 1)[1].strip()
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


def phase_note_make(clippings: list[dict], timeout: int = 3600) -> list[dict]:
    """Phase 4: Generate notes using OpenRouter (auto-routed free models)."""
    logger.info(f"=== Phase 4: Note Maker ({len(clippings)} clippings) ===")

    successful_clippings = [c for c in clippings if c.get("status") == "success" and c.get("clipping_path")]
    # Filter out empty/failed clippings
    valid_clippings = [c for c in successful_clippings if not _is_empty_clipping(c["clipping_path"])]
    skipped = len(successful_clippings) - len(valid_clippings)
    if skipped:
        logger.warning(f"Skipping {skipped} empty/failed clippings")
        
    # Load already processed clippings
    processed_paths = set()
    note_maker_done = WORKSPACE_DIR / "note_maker_done.jsonl"
    if note_maker_done.exists():
        with open(note_maker_done) as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    if "clipping_path" in data:
                        processed_paths.add(Path(data["clipping_path"]).name)
                except Exception:
                    pass
                    
    valid_clippings = [c for c in valid_clippings if Path(c["clipping_path"]).name not in processed_paths]
    logger.info(f"Skipping already processed clippings. Remaining to process: {len(valid_clippings)}")

    successful_clippings = valid_clippings
    if not successful_clippings:
        logger.warning("No successful clippings to process")
        return []

    api_key = os.environ.get("OPENROUTER_API_KEY") or _load_dotenv_value("OPENROUTER_API_KEY")
    if not api_key:
        logger.error("OPENROUTER_API_KEY environment variable is not set. Please set it to use the API pipeline.")
        return [{"status": "error", "error": "OPENROUTER_API_KEY not set"}]

    format_file = PROJECT_DIR / ".claude" / "skills" / "note-pipeline" / "references" / "note-format.md"
    if not format_file.exists():
        logger.error(f"Format rules file not found at {format_file}")
        return [{"status": "error", "error": "format file not found"}]
        
    format_content = format_file.read_text(errors="ignore")
    
    client = OpenAI(
        base_url="https://api.deepseek.com",
        api_key="sk-9d9b0f2dfe3b42df8fdafb1017196fc2",
        timeout=1800.0,
    )
    # Use official DeepSeek API
    model_name = "deepseek-reasoner"

    results = []

    for idx, c in enumerate(successful_clippings):
        clipping_path = c['clipping_path']
        domain_hint = c.get('domain_hint', 'general')
        logger.info(f"Processing clipping {idx+1}/{len(successful_clippings)}: {clipping_path}")

        try:
            clipping_content = Path(PROJECT_DIR / clipping_path).read_text(errors="ignore")
        except Exception as e:
            logger.error(f"Failed to read {clipping_path}: {e}")
            continue

        prompt = f"""다음 clipping 파일을 6 Rs 파이프라인으로 처리하고, 아래의 note-format.md 규칙을 엄격히 준수하여 위키 노트 1개와 가든 노트 1~3개를 마크다운으로 생성하라.
**반드시 모든 본문 내용과 제목은 한국어(Korean)로 작성할 것.**

[추론 및 통찰력 요구사항]
단순 요약을 넘어, 표면적으로 드러나지 않은 숨겨진 의도, 비약적인 맥락 연결, 또는 전문가 수준의 날카로운 통찰력(Insight)을 반드시 도출하라. 
<think> 태그 내에서 충분히 깊게 다각도로 고민한 뒤, 최종 결과물만 아래 포맷으로 출력하라.

[절대 지켜야 할 파일 출력 포맷]
각 노트는 반드시 아래 예시처럼 '경로 헤딩(####)' 아래에 '마크다운 블록(```markdown)'을 두는 구조로 출력해야 파서가 인식할 수 있다. 
예시 포맷:
#### `wiki/concepts/20260512-test-note.md`
```markdown
---
title: 테스트 노트
...
```

[note-format.md 규칙]
{format_content}

[처리할 clipping 파일]
- 파일명: {Path(clipping_path).name}
- 내용:
{clipping_content}

작업 순서:
1. 이 clipping에 대해 R1(Reduce) → R2(Reflect) → R3(Reweave) → R4(Record) → R5(Verify) 실행 (내부적으로 사고)
2. 이 clipping을 기반으로 wiki/ 노트 1개 + garden/ 노트 1~3개를 생성
3. **매우 중요**: 각 노트를 생성할 때, 다음과 같이 파일 경로를 명시하고 마크다운 코드 블록으로 감싸서 출력할 것.

예시:
`wiki/concepts/20260605-example-concept.md`
```markdown
---
(프론트매터 내용)
---
(본문 내용)
```

**위키링크 규칙 (반드시 준수):**
- 위키링크는 순수 슬러그만 사용: `[[슬러그]]` 형식
- path-prefixed 위키링크 금지: `[[concepts/슬러그]]`, `[[entities/슬러그]]` 사용 금지
"""

        logger.info(f"Invoking OpenRouter ({model_name}) for {Path(clipping_path).name}...")
        try:
            completion = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=1800, # 30 minutes timeout for API call (for large local models)
            )
            response_text = completion.choices[0].message.content
            
            # Parse the response and save files (robust regex for Qwen outputs)
            pattern = re.compile(r'(?:#+\s*)?(?:`|\*\*|\[)?(wiki/[^\s`\*\[\]]+?\.md|garden/[^\s`\*\[\]]+?\.md)(?:`|\*\*|\])?\s*```(?:markdown)?\n(.*?)\n```', re.DOTALL)
            matches = pattern.findall(response_text)
            
            saved_files = []
            for file_path_str, content in matches:
                full_path = PROJECT_DIR / file_path_str.strip()
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content.strip() + "\n")
                saved_files.append(file_path_str.strip())
                logger.info(f"  -> Saved {file_path_str.strip()}")
                
            if not saved_files:
                logger.warning(f"No valid markdown blocks parsed from the response for {clipping_path}")
                # Fallback logging if parsing fails
                failed_log = WORKSPACE_DIR / f"failed_parse_{Path(clipping_path).stem}.md"
                failed_log.write_text(response_text)
                logger.warning(f"Saved raw response to {failed_log}")
                
            # Log success
            results.append({
                "clipping_path": str(clipping_path),
                "generated_files": saved_files,
                "status": "success"
            })
            
            # Append to note_maker_done.jsonl
            with open(note_maker_done, "a") as f:
                f.write(json.dumps({"clipping_path": str(clipping_path), "status": "success", "generated_files": saved_files}) + "\n")
                
        except Exception as e:
            logger.error(f"OpenRouter API failed for {clipping_path}: {e}")
            results.append({
                "clipping_path": str(clipping_path),
                "status": "error",
                "error": str(e)
            })

        # Respect rate limits for free models
        if idx < len(successful_clippings) - 1:
            logger.info("Waiting 10 seconds before next clipping to respect OpenRouter rate limits...")
            time.sleep(10)

    logger.info(f"Note maker completed: processed {len(results)} clippings.")
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

    # Step 3: Push 0004_Wikis content to GitHub (GitHub Actions pulls from this repo)
    logger.info("Pushing content to GitHub...")
    run_cmd(["git", "add", "wiki/", "garden/", "clippings/", "scripts/", "index.md"],
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


def phase_report(ingest_results: list, note_results: list, publish_success):
    """Phase 6: Generate summary report and send Telegram notification."""
    logger.info("=== Phase 6: Report ===")

    successful_clips = len([r for r in ingest_results if r.get("status") == "success"])
    failed_clips = len([r for r in ingest_results if r.get("status") == "error"])
    notes_created = len(note_results)
    _, publish_text = _publish_label(publish_success)

    report = f"""
파이프라인 실행 결과 — {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'=' * 50}
- 수신: {len(ingest_results)}개 항목
- 클리핑: {successful_clips}개 완료 / {failed_clips}개 실패
- 노트 생성: {notes_created}개
- 퍼블리시: {publish_text}
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
    parser.add_argument("--timeout", type=int, default=3600,
                        help="Timeout in seconds for Phase 4 Claude Code (default: 3600)")
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
            print(f"[DRY RUN] Would invoke Claude Code for note generation")
        else:
            # Use ingestor_done.jsonl if available, otherwise scan clippings
            done_file = WORKSPACE_DIR / "ingestor_done.jsonl"
            if done_file.exists():
                with open(done_file) as f:
                    clippings = [json.loads(line.strip()) for line in f]
            else:
                clippings = ingest_results
                
            if not clippings:
                logger.info("ingestor_done.jsonl not found and no ingest results. Falling back to scanning clippings directory.")
                clippings = []
                for f in CLIPPINGS_DIR.glob("*.md"):
                    clippings.append({
                        "clipping_path": str(f.relative_to(PROJECT_DIR)),
                        "status": "success",
                        "domain_hint": "general"
                    })

            if clippings:
                note_results = phase_note_make(clippings, timeout=args.timeout)
            else:
                logger.warning("No clippings to process for note generation")

    # Skip Phase 5 if no notes were created AND Phase 4 was also requested
    if 5 in phases and 4 in phases and not note_results:
        if not args.dry_run:
            logger.info("No notes created — skipping publish (nothing new to deploy)")
            publish_success = None  # skipped, not failed
            phases.discard(5)

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

    # Phase 6: Report — always runs even if earlier phases crashed
    if 6 in phases and not args.dry_run:
        try:
            phase_report(ingest_results, note_results, publish_success)
        except Exception as e:
            logger.error(f"phase_report crashed: {e}")

    logger.info("Pipeline complete")


if __name__ == "__main__":
    main()