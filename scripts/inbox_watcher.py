"""
Inbox folder watcher - auto-triggers pipeline on new files
Usage: python3 inbox_watcher.py
Templated version — set CLAUDE_CLI_PATH in .env if needed
"""
import os
import time
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

load_dotenv(Path(__file__).parent / ".env")

INBOX_DIR = Path(__file__).parent.parent / "inbox"
PROJECT_DIR = Path(__file__).parent.parent
CLAUDE_CLI_PATH = os.environ.get("CLAUDE_CLI_PATH", "claude")

logging.basicConfig(
    filename=Path(__file__).parent / "watcher.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)

DEBOUNCE_SECONDS = 30  # wait 30s after file addition before triggering pipeline


class InboxHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending = False
        self._last_event = 0

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix in [".md", ".txt", ".yaml", ".pdf"] and "inbox" in str(path):
            logging.info(f"New file detected: {path}")
            self._pending = True
            self._last_event = time.time()

    def trigger_if_ready(self):
        if self._pending and (time.time() - self._last_event) >= DEBOUNCE_SECONDS:
            self._pending = False
            logging.info("Triggering pipeline")
            subprocess.Popen(
                [CLAUDE_CLI_PATH, "-p", "wiki-orchestrator: process inbox pipeline"],
                cwd=str(PROJECT_DIR),
            )


def main():
    handler = InboxHandler()

    # check for existing pending files at startup
    existing = list(INBOX_DIR.rglob("*.md")) + list(INBOX_DIR.rglob("*.pdf"))
    pending = [f for f in existing if "status: pending" in f.read_text(errors="ignore")]
    if pending:
        logging.info(f"Found {len(pending)} pending files — scheduling pipeline trigger")
        handler._pending = True
        handler._last_event = time.time() - DEBOUNCE_SECONDS  # trigger immediately

    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=True)
    observer.start()
    logging.info(f"Inbox watcher started: {INBOX_DIR}")
    try:
        while True:
            time.sleep(5)
            handler.trigger_if_ready()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()