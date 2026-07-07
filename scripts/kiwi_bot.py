"""
Kiwi Telegram Bot — inbox 수신 게이트웨이
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import telebot
import subprocess

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = set(
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip()
)
INBOX_DIR = Path(os.environ.get("INBOX_DIR", Path(__file__).parent.parent / "inbox"))
WORKSPACE = INBOX_DIR.parent / "_workspace"
LOCK_FILE = Path(__file__).parent / ".pipeline.lock"
VENV_PYTHON = str(Path(__file__).parent.parent / ".venv" / "bin" / "python3")
if not Path(VENV_PYTHON).exists():
    ALT_PYTHON = Path.home() / "Projects" / "ultimate-fetcher" / ".venv" / "bin" / "python3"
    if ALT_PYTHON.exists():
        VENV_PYTHON = str(ALT_PYTHON)
TELEGRAM_AGENT_ENABLED = os.environ.get("TELEGRAM_AGENT_ENABLED", "1").lower() not in {"0", "false", "no"}

from logging.handlers import RotatingFileHandler

log_path = Path(__file__).parent / "kiwi_bot.log"
log_handler = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5)
log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[log_handler])

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def detect_type(text, has_doc: bool) -> str:
    if has_doc:
        return "pdf"
    if not text:
        return "raw"
    urls = [w for w in (text or "").split() if w.startswith("http")]
    if not urls:
        return "raw"
    if any("x.com" in u or "twitter.com" in u for u in urls):
        return "thread"
    if any("youtube.com" in u or "youtu.be" in u for u in urls):
        return "video"
    return "url"


def save_url_meta(text: str, sender: str, caption: str, content_type: str = "url") -> Path:
    ts = timestamp_slug()
    target_dir = INBOX_DIR / "urls"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{ts}_url.md"
    frontmatter = f"---\nsource_url: {text.strip()}\nreceived_at: {datetime.now().isoformat()}\nsender: {sender}\ntype: {content_type}\nstatus: pending\n---\n"
    body = f"\n{caption}\n" if caption else ""
    path.write_text(frontmatter + body)
    return path


def save_thread_meta(url: str, sender: str) -> Path:
    ts = timestamp_slug()
    target_dir = INBOX_DIR / "threads"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{ts}_thread.md"
    content = f"""---
source_url: {url}
received_at: {datetime.now().isoformat()}
sender: {sender}
type: thread
status: pending
---
"""
    path.write_text(content)
    return path


def save_raw_text(text: str, sender: str) -> Path:
    ts = timestamp_slug()
    target_dir = INBOX_DIR / "raw"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{ts}_raw.txt"
    meta_path = target_dir / f"{ts}_raw.meta.yaml"
    path.write_text(text)
    meta_path.write_text(
        f"received_at: {datetime.now().isoformat()}\nsender: {sender}\ntype: raw\nstatus: pending\n"
    )
    return path


def append_workspace_log(file_path: Path, content_type: str) :
    INBOX_DIR.mkdir(exist_ok=True, parents=True)
    log_path = INBOX_DIR / "kiwi_new_files.jsonl"
    entry = {"inbox_path": str(file_path.relative_to(INBOX_DIR.parent)), "type": content_type, "status": "pending", "received_at": datetime.now().isoformat()}
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_inbox_log(file_path: Path, content_type: str, sender: str) :
    log_path = INBOX_DIR / "inbox.log"
    with open(log_path, "a") as f:
        f.write(f"{datetime.now().isoformat()} | {content_type} | {sender} | {file_path}\n")


def trigger_telegram_agent(file_path: Path, content_type: str, sender: str) -> None:
    """Queue a Telegram-originated item for the agent workflow."""
    if not TELEGRAM_AGENT_ENABLED:
        return
    # Pause switch (US-002): while scripts/.pipeline_paused exists, Telegram input
    # is still received/saved to inbox, but the downstream pipeline is NOT triggered.
    if (Path(__file__).parent / ".pipeline_paused").exists():
        logging.info("Pipeline auto-trigger paused (scripts/.pipeline_paused present); inbox saved, not queuing agent.")
        return
    python_bin = VENV_PYTHON if Path(VENV_PYTHON).exists() else "python3"
    try:
        rel_path = str(file_path.relative_to(INBOX_DIR.parent))
    except ValueError:
        rel_path = str(file_path)
    try:
        subprocess.Popen(
            [
                python_bin,
                str(Path(__file__).parent / "telegram_agent.py"),
                "--event-path",
                rel_path,
                "--event-type",
                content_type,
                "--sender",
                sender,
            ],
            cwd=str(INBOX_DIR.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logging.info(f"Telegram agent queued: {rel_path} ({content_type})")
    except Exception as e:
        logging.error(f"Telegram agent trigger failed: {e}")


HELP_TEXT = (
    "📥 *Hermes wiki agent*\n\n"
    "• URL → 자동 저장\n"
    "• URL + 텍스트 → 출처와 메모 함께 저장\n"
    "• X.com/Twitter URL → 자동 저장\n"
    "• PDF 파일 → 자동 저장\n"
    "• `/status` → 대기 파일 수 확인\n"
    "• `/publish` → 변경 사항 강제 퍼블리시\n"
    "• 저장된 항목은 Hermes 에이전트가 파이프라인 처리와 웹 배포까지 진행\n\n"
    "⚠️ URL 없는 텍스트만 보내면 저장되지 않습니다."
)

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    logging.info(f"Received start command from User ID: {message.from_user.id}")
    if not is_allowed(message.from_user.id):
        logging.warning(f"Unauthorized access attempt from {message.from_user.id}")
        return
    try:
        bot.reply_to(message, HELP_TEXT, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"reply error: {e}")




@bot.message_handler(commands=["status"])
def handle_status(message):
    if not is_allowed(message.from_user.id):
        return
    pending = sum(
        1 for d in ["urls", "pdfs", "threads", "raw"]
        for f in (INBOX_DIR / d).glob("*")
        if f.suffix in [".md", ".txt", ".yaml", ".pdf"]
    )
    bot.reply_to(message, f"대기 중인 파일: {pending}개")


@bot.message_handler(commands=["publish"])
def handle_publish(message):
    if not is_allowed(message.from_user.id):
        return
    import time
    import subprocess
    # Check if pipeline is already running
    if LOCK_FILE.exists():
        lock_age = time.time() - LOCK_FILE.stat().st_mtime
        if lock_age < 1800:
            bot.reply_to(message, f"⚠️ 파이프라인이 이미 실행 중입니다. (진행 시간: {int(lock_age)}초)")
            return
        else:
            LOCK_FILE.unlink(missing_ok=True)
            
    bot.reply_to(message, "🚀 수동 퍼블리시(Phase 5-6)를 트리거합니다...")
    python_bin = VENV_PYTHON if Path(VENV_PYTHON).exists() else "python3"
    
    subprocess.Popen(
        [python_bin, str(Path(__file__).parent / "pipeline.py"), "--phase", "5-6"],
        cwd=str(INBOX_DIR.parent)
    )


@bot.message_handler(content_types=["document"])
def handle_document(message):
    if not is_allowed(message.from_user.id):
        return
    sender = message.from_user.username or str(message.from_user.id)
    ts = timestamp_slug()
    fname = message.document.file_name or ""
    caption = message.caption or ""

    file_info = bot.get_file(message.document.file_id)
    downloaded = bot.download_file(file_info.file_path)

    # .txt / .md 파일 → texts 폴더로 라우팅 (트랜스크립트, 메모 등)
    if fname.lower().endswith((".txt", ".md")):
        target_dir = INBOX_DIR / "texts"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"{ts}_{fname}"
        file_path.write_bytes(downloaded)

        # 캡션에서 YouTube URL 추출
        source_url = next((w for w in caption.split() if w.startswith("http")), "")
        title = caption.replace(source_url, "").strip() if source_url else caption.strip()
        if not title:
            title = fname.replace(".txt", "").replace(".md", "").replace("_", " ")

        meta_path = target_dir / f"{ts}_{fname}.meta.yaml"
        meta_path.write_text(
            f"received_at: {datetime.now().isoformat()}\nsender: {sender}\n"
            f"original_name: {fname}\ntitle: {title}\n"
            f"source_url: {source_url}\ntype: text\nstatus: pending\n"
        )
        append_workspace_log(file_path, "text")
        append_inbox_log(file_path, "text", sender)
        trigger_telegram_agent(file_path, "text", sender)
        try:
            hint = f"\n소스 URL: {source_url}" if source_url else "\n⚠️ 캡션에 YouTube URL을 포함하면 출처가 연결됩니다."
            bot.reply_to(message, f"텍스트 파일 저장 완료: {file_path.name}{hint}")
        except Exception as e:
            logging.error(f"reply error: {e}")
        logging.info(f"Text saved: {file_path}")
        return

    # 이미지 파일 라우팅
    if fname.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
        target_dir = INBOX_DIR / "screenshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        img_path = target_dir / f"{ts}_{fname}"
        img_path.write_bytes(downloaded)

        meta_path = target_dir / f"{ts}_{fname}.meta.yaml"
        meta_path.write_text(
            f"received_at: {datetime.now().isoformat()}\nsender: {sender}\n"
            f"original_name: {fname}\ncaption: {caption}\n"
            f"type: image\nstatus: pending\n"
        )
        append_workspace_log(img_path, "image")
        append_inbox_log(img_path, "image", sender)
        trigger_telegram_agent(img_path, "image", sender)
        try:
            bot.reply_to(message, f"이미지 저장 완료: {img_path.name}")
        except Exception as e:
            logging.error(f"reply error: {e}")
        logging.info(f"Image saved: {img_path}")
        return

    # PDF 및 나머지 기타 문서 → 기존 처리
    target_dir = INBOX_DIR / "pdfs"
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = target_dir / f"{ts}_{fname}"
    pdf_path.write_bytes(downloaded)

    meta_path = target_dir / f"{ts}_{fname}.meta.yaml"
    meta_path.write_text(
        f"received_at: {datetime.now().isoformat()}\nsender: {sender}\n"
        f"original_name: {fname}\ncaption: {caption}\n"
        f"type: pdf\nstatus: pending\n"
    )

    append_workspace_log(pdf_path, "pdf")
    append_inbox_log(pdf_path, "pdf", sender)
    trigger_telegram_agent(pdf_path, "pdf", sender)
    try:
        bot.reply_to(message, f"PDF 저장 완료: {pdf_path.name}")
    except Exception as e:
        logging.error(f"reply error: {e}")
    logging.info(f"PDF saved: {pdf_path}")


@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    if not is_allowed(message.from_user.id):
        return
    sender = message.from_user.username or str(message.from_user.id)
    ts = timestamp_slug()
    caption = message.caption or ""

    # Telegram sends multiple sizes; [-1] is the largest
    photo = message.photo[-1]
    file_info = bot.get_file(photo.file_id)
    downloaded = bot.download_file(file_info.file_path)

    ext = Path(file_info.file_path).suffix if "." in file_info.file_path else ".jpg"
    fname = f"photo_{photo.file_unique_id}{ext}"

    target_dir = INBOX_DIR / "screenshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    img_path = target_dir / f"{ts}_{fname}"
    img_path.write_bytes(downloaded)

    meta_path = target_dir / f"{ts}_{fname}.meta.yaml"
    meta_path.write_text(
        f"received_at: {datetime.now().isoformat()}\nsender: {sender}\n"
        f"original_name: {fname}\ncaption: {caption}\n"
        f"type: image\nstatus: pending\n"
    )
    append_workspace_log(img_path, "image")
    append_inbox_log(img_path, "image", sender)
    trigger_telegram_agent(img_path, "image", sender)
    try:
        bot.reply_to(message, f"이미지 저장 완료: {img_path.name}")
    except Exception as e:
        logging.error(f"reply error: {e}")
    logging.info(f"Photo saved: {img_path}")


@bot.message_handler(content_types=["text"])
def handle_text(message):
    logging.info(f"Received text from User ID: {message.from_user.id}")
    if not is_allowed(message.from_user.id):
        logging.warning(f"Unauthorized text attempt from {message.from_user.id}")
        return
    text = message.text.strip()
    sender = message.from_user.username or str(message.from_user.id)
    content_type = detect_type(text, has_doc=False)

    if content_type == "raw":
        # URL 없는 순수 텍스트는 저장하지 않음
        try:
            bot.reply_to(message, "URL이 포함된 내용만 저장됩니다.\nURL + 설명을 함께 보내주세요.", parse_mode="Markdown")
        except Exception as e:
            logging.error(f"reply error: {e}")
        return

    urls = [w for w in text.split() if w.startswith("http")]

    # 줄 단위로 URL과 구분자 제거해 멀티라인 캡션 보존
    caption_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        # 구분자 줄 제거 (---, —-, ===, 빈 줄 유지는 허용)
        if stripped in ("---", "—-", "===", "—--"):
            continue
        # URL만 있는 줄 또는 "출처 url: URL" 패턴 줄 제거
        words = stripped.split()
        if all(w.startswith("http") for w in words if w):
            continue
        if len(words) >= 2 and words[-1].startswith("http") and "출처" in stripped:
            continue
        # URL 단어만 줄 내에서 제거
        cleaned = " ".join(w for w in words if not w.startswith("http")).strip()
        caption_lines.append(cleaned)

    # 앞뒤 빈 줄 제거
    while caption_lines and not caption_lines[0]:
        caption_lines.pop(0)
    while caption_lines and not caption_lines[-1]:
        caption_lines.pop()
    caption = "\n".join(caption_lines) if caption_lines else None

    if content_type == "thread":
        file_path = save_thread_meta(urls[0], sender)
    else:
        file_path = save_url_meta(urls[0], sender, caption=caption or "", content_type=content_type)

    append_workspace_log(file_path, content_type)
    append_inbox_log(file_path, content_type, sender)
    trigger_telegram_agent(file_path, content_type, sender)
    label = {"url": "🔗 URL", "video": "🎬 영상", "thread": "🧵 X 스레드"}.get(content_type, "📄")
    try:
        note = f" ({len(caption_lines)}줄 메모)" if caption_lines else ""
        bot.reply_to(message, f"✅ {label} 저장 완료: {file_path.name}{note}")
    except Exception as e:
        logging.error(f"reply error: {e}")
    logging.info(f"Saved {content_type}: {file_path}")


if __name__ == "__main__":
    import time
    import fcntl

    LOCK_FILE = Path(__file__).parent / ".kiwi_bot.lock"

    # Single-instance guard: exit immediately if another instance holds the lock
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip() or "0")
            if old_pid > 0:
                os.kill(old_pid, 0)
                logging.warning(f"Another kiwi_bot instance is already running — pid {old_pid}")
                raise SystemExit(0)
        except ProcessLookupError:
            LOCK_FILE.unlink(missing_ok=True)
        except ValueError:
            LOCK_FILE.unlink(missing_ok=True)
        except PermissionError:
            logging.warning("Another kiwi_bot instance appears to be running but is not inspectable")
            raise SystemExit(0)

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logging.warning("Another kiwi_bot instance is already running — exiting")
        raise SystemExit(0)

    lock_fd.write(str(os.getpid()))
    lock_fd.flush()

    logging.info("Kiwi bot starting...")
    # Delete webhook and wait for Telegram to release the previous long-poll session
    for attempt in range(5):
        try:
            bot.delete_webhook()
            break
        except Exception as e:
            logging.warning(f"delete_webhook attempt {attempt+1}: {e}")
            time.sleep(3)

    # Small delay so Telegram can release any previous getUpdates session
    time.sleep(5)

    while True:
        try:
            bot.polling(
                none_stop=False,
                interval=2,
                timeout=30,
                long_polling_timeout=25,
            )
        except Exception as e:
            err_str = str(e)
            try:
                bot.stop_polling()
            except Exception:
                pass
            if "409" in err_str or "Conflict" in err_str:
                # Another instance grabbed the session — wait and retry
                logging.warning(f"409 conflict, waiting 35s before retry: {e}")
                time.sleep(35)
            else:
                logging.error(f"Polling error, retrying in 5s: {e}")
                time.sleep(5)
