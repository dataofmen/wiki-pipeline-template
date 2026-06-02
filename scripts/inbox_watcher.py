"""
Inbox 폴더 감시 → 파이프라인 자동 트리거
사용법: python3 inbox_watcher.py

Python 파이프라인(Phases 3-6)을 직접 실행하고,
Note Maker(Phase 4)만 Claude Code에 위임합니다.
"""
import time
import subprocess
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

INBOX_DIR = Path(__file__).parent.parent / "inbox"
PROJECT_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
LOCK_FILE = SCRIPTS_DIR / ".pipeline.lock"
VENV_PYTHON = str(Path(__file__).parent.parent / ".venv" / "bin" / "python3")
# Prefer project venv, fall back to ultimate-fetcher venv, then system python
if not Path(VENV_PYTHON).exists():
    ALT_PYTHON = Path.home() / "Projects" / "ultimate-fetcher" / ".venv" / "bin" / "python3"
    if ALT_PYTHON.exists():
        VENV_PYTHON = str(ALT_PYTHON)

logging.basicConfig(
    filename=Path(__file__).parent / "watcher.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)

DEBOUNCE_SECONDS = 30  # 파일 추가 후 30초 대기 후 파이프라인 실행


class InboxHandler(FileSystemEventHandler):
    def __init__(self):
        self._pending = False
        self._last_event = 0

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix in [".md", ".txt", ".yaml", ".pdf"] and "inbox" in str(path):
            logging.info(f"새 파일 감지: {path}")
            self._pending = True
            self._last_event = time.time()

    def trigger_if_ready(self):
        if self._pending and (time.time() - self._last_event) >= DEBOUNCE_SECONDS:
            self._pending = False
            # Lock file to prevent concurrent pipeline runs
            if LOCK_FILE.exists():
                lock_age = time.time() - LOCK_FILE.stat().st_mtime
                if lock_age < 600:  # Lock is less than 10 min old
                    logging.info("Pipeline already running, skipping trigger.")
                    return
                else:
                    logging.warning("Stale lock file detected (>10 min), removing.")
                    LOCK_FILE.unlink(missing_ok=True)

            LOCK_FILE.touch()
            python_bin = VENV_PYTHON if Path(VENV_PYTHON).exists() else "python3"
            logging.info(f"Python 파이프라인 트리거 ({python_bin})")
            subprocess.Popen(
                [python_bin, str(SCRIPTS_DIR / "pipeline.py")],
                cwd=str(PROJECT_DIR),
            )


def main():
    handler = InboxHandler()

    # 시작 시 기존 pending 파일 확인
    existing = list(INBOX_DIR.rglob("*.md")) + list(INBOX_DIR.rglob("*.pdf"))
    pending = [f for f in existing if "status: pending" in f.read_text(errors="ignore")]
    if pending:
        logging.info(f"기존 pending 파일 {len(pending)}개 감지 — 파이프라인 트리거 예약")
        handler._pending = True
        handler._last_event = time.time() - DEBOUNCE_SECONDS  # 즉시 트리거

    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=True)
    observer.start()
    logging.info(f"Inbox 감시 시작: {INBOX_DIR}")
    try:
        while True:
            time.sleep(5)
            handler.trigger_if_ready()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()