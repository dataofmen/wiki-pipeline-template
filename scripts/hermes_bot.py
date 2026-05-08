"""
Hermes Telegram Bot - inbox reception gateway
Templated version — set BOT_NAME in .env to customize display name
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
BOT_NAME = os.environ.get("BOT_NAME", "{{BOT_NAME}}")
ALLOWED_USERS = set(
    int(uid.strip())
    for uid in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip()
)
INBOX_DIR = Path(os.environ.get("INBOX_DIR", Path(__file__).parent.parent / "inbox"))
WORKSPACE = INBOX_DIR.parent / "_workspace"

logging.basicConfig(
    filename=Path(__file__).parent / "hermes_bot.log",
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
    if text.strip().startswith("http") and len(text.split()) == 1:
        return "url"
    return "url"


def save_url_meta(text: str, sender: str, caption) -> Path:
    ts = timestamp_slug()
    target_dir = INBOX_DIR / "urls"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{ts}_url.md"
    content = f"""---
source_url: {text.strip()}
received_at: {datetime.now().isoformat()}
sender: {sender}
caption: {caption or ""}
type: url
status: pending
---
"""
    path.write_text(content)
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


def append_workspace_log(file_path: Path, content_type: str):
    WORKSPACE.mkdir(exist_ok=True)
    log_path = WORKSPACE / "hermes_new_files.jsonl"
    entry = {"path": str(file_path), "type": content_type, "received_at": datetime.now().isoformat()}
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_inbox_log(file_path: Path, content_type: str, sender: str):
    log_path = INBOX_DIR / "inbox.log"
    with open(log_path, "a") as f:
        f.write(f"{datetime.now().isoformat()} | {content_type} | {sender} | {file_path}\n")


HELP_TEXT = (
    f"📥 *{BOT_NAME} wiki bot*\n\n"
    "• URL → auto-save\n"
    "• URL + text → save with source and note\n"
    "• X.com/Twitter URL → auto-save\n"
    "• PDF file → auto-save\n"
    "• `/status` → check pending file count\n\n"
    "⚠️ Text without a URL will not be saved."
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
    bot.reply_to(message, f"Pending files: {pending}")


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
        bot.reply_to(message, f"PDF saved: {pdf_path.name}")
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
        # Text without URL is not saved
        try:
            bot.reply_to(message, "Only content with URLs is saved.\nPlease send a URL with description.", parse_mode="Markdown")
        except Exception as e:
            logging.error(f"reply error: {e}")
        return

    urls = [w for w in text.split() if w.startswith("http")]
    # Use non-URL parts as caption
    caption = " ".join(w for w in text.split() if not w.startswith("http")).strip() or None

    if content_type == "thread":
        file_path = save_thread_meta(urls[0], sender)
    else:
        file_path = save_url_meta(urls[0], sender, caption=caption)

    append_workspace_log(file_path, content_type)
    append_inbox_log(file_path, content_type, sender)
    label = "🔗 URL" if content_type == "url" else "🧵 X thread"
    try:
        bot.reply_to(message, f"✅ {label} saved: {file_path.name}")
    except Exception as e:
        logging.error(f"reply error: {e}")
    logging.info(f"Saved {content_type}: {file_path}")


if __name__ == "__main__":
    import time
    logging.info(f"{BOT_NAME} bot starting...")
    try:
        bot.delete_webhook()
    except Exception:
        pass
    bot.polling(none_stop=True, interval=2, timeout=10, long_polling_timeout=8)