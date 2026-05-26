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

load_dotenv(Path(__file__).parent / ".env")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USERS = set(
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip()
)
INBOX_DIR = Path(os.environ.get("INBOX_DIR", Path(__file__).parent.parent / "inbox"))
WORKSPACE = INBOX_DIR.parent / "_workspace"

logging.basicConfig(
    filename=Path(__file__).parent / "kiwi_bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

bot = telebot.TeleBot(BOT_TOKEN)


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
    WORKSPACE.mkdir(exist_ok=True)
    log_path = WORKSPACE / "kiwi_new_files.jsonl"
    entry = {"path": str(file_path), "type": content_type, "received_at": datetime.now().isoformat()}
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_inbox_log(file_path: Path, content_type: str, sender: str) :
    log_path = INBOX_DIR / "inbox.log"
    with open(log_path, "a") as f:
        f.write(f"{datetime.now().isoformat()} | {content_type} | {sender} | {file_path}\n")


HELP_TEXT = (
    "📥 *dataofmen wiki 봇*\n\n"
    "• URL → 자동 저장\n"
    "• URL + 텍스트 → 출처와 메모 함께 저장\n"
    "• X.com/Twitter URL → 자동 저장\n"
    "• PDF 파일 → 자동 저장\n"
    "• `/status` → 대기 파일 수 확인\n\n"
    "⚠️ URL 없는 텍스트만 보내면 저장되지 않습니다."
)

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if not is_allowed(message.from_user.id):
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


@bot.message_handler(content_types=["document"])
def handle_document(message):
    if not is_allowed(message.from_user.id):
        return
    sender = message.from_user.username or str(message.from_user.id)
    ts = timestamp_slug()
    target_dir = INBOX_DIR / "pdfs"
    target_dir.mkdir(parents=True, exist_ok=True)

    file_info = bot.get_file(message.document.file_id)
    downloaded = bot.download_file(file_info.file_path)
    pdf_path = target_dir / f"{ts}_{message.document.file_name}"
    pdf_path.write_bytes(downloaded)

    meta_path = target_dir / f"{ts}_{message.document.file_name}.meta.yaml"
    meta_path.write_text(
        f"received_at: {datetime.now().isoformat()}\nsender: {sender}\n"
        f"original_name: {message.document.file_name}\ncaption: {message.caption or ''}\n"
        f"type: pdf\nstatus: pending\n"
    )

    append_workspace_log(pdf_path, "pdf")
    append_inbox_log(pdf_path, "pdf", sender)
    try:
        bot.reply_to(message, f"PDF 저장 완료: {pdf_path.name}")
    except Exception as e:
        logging.error(f"reply error: {e}")
    logging.info(f"PDF saved: {pdf_path}")


@bot.message_handler(content_types=["text"])
def handle_text(message):
    if not is_allowed(message.from_user.id):
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
            bot.polling(none_stop=False, interval=2, timeout=30, long_polling_timeout=25)
        except Exception as e:
            err_str = str(e)
            if "409" in err_str or "Conflict" in err_str:
                # Another instance grabbed the session — wait and retry
                logging.warning(f"409 conflict, waiting 15s before retry: {e}")
                time.sleep(15)
            else:
                logging.error(f"Polling error, retrying in 5s: {e}")
                time.sleep(5)
