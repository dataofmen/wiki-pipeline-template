"""
URL 원문 추출 스크립트
사용법: python3 fetch_url.py "https://..." [--js]
"""
import sys
import re
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse


def slug_from_title(title: str) -> str:
    title = re.sub(r"[^\w\s-]", "", title.lower())
    words = title.split()[:5]
    return "-".join(words)


def fetch_via_jina(url: str):
    try:
        import requests
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=30,
                            headers={"Accept": "text/markdown"})
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text
    except Exception:
        pass
    return None


def fetch_via_readability(url: str):
    try:
        import requests
        from readability import Document
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        doc = Document(resp.text)
        title = doc.title()
        content = doc.summary()
        # HTML → plain text 기본 변환
        content = re.sub(r"<[^>]+>", "", content)
        content = re.sub(r"\n{3,}", "\n\n", content)
        return title, content.strip()
    except Exception as e:
        print(f"readability 실패: {e}", file=sys.stderr)
    return None


def fetch_via_playwright(url: str):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()
            content = page.inner_text("body")
            browser.close()
            return title, content
    except Exception as e:
        print(f"playwright 실패: {e}", file=sys.stderr)
    return None


def detect_domain_hint(url: str, title: str, content: str) -> str:
    text = f"{url} {title} {content[:500]}".lower()
    if any(k in text for k in ["trading", "invest", "stock", "crypto", "quant", "backtest", "sharpe"]):
        return "trading"
    if any(k in text for k in ["ux", "hci", "usability", "user research", "product", "design", "figma"]):
        return "ux"
    if any(k in text for k in ["note", "knowledge", "pkm", "zettelkasten", "philosophy", "essay", "thinking"]):
        return "pkm"
    return "general"


def save_clipping(url: str, title: str, content: str, method: str, output_dir: Path) -> Path:
    date = datetime.now().strftime("%Y%m%d")
    slug = slug_from_title(title) or urlparse(url).netloc.replace(".", "-")
    filename = f"{date}-{slug}.md"
    domain_hint = detect_domain_hint(url, title, content)
    word_count = len(content.split())

    front_matter = f"""---
source_url: {url}
source_type: url
clipped_at: {datetime.now().isoformat()}
title: {title}
author: ""
word_count: {word_count}
fetch_method: {method}
status: clipped
domain_hint: {domain_hint}
tags: []
---

{content}

---
*원문: [{url}]({url})*
"""
    path = output_dir / filename
    path.write_text(front_matter)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--js", action="store_true", help="JavaScript 렌더링 사용")
    parser.add_argument("--output", default=None, help="저장 디렉토리")
    args = parser.parse_args()

    output_dir = Path(args.output) if args.output else Path(__file__).parent.parent / "clippings"
    output_dir.mkdir(exist_ok=True)

    url = args.url
    title, content, method = "", "", "unknown"

    if not args.js:
        jina_result = fetch_via_jina(url)
        if jina_result:
            lines = jina_result.splitlines()
            raw_title = lines[0].lstrip("# ").strip() if lines else url
            title = re.sub(r"^Title:\s*", "", raw_title).strip()
            content = jina_result
            method = "jina"
        else:
            result = fetch_via_readability(url)
            if result:
                title, content = result
                method = "readability"

    if not content:
        result = fetch_via_playwright(url)
        if result:
            title, content = result
            method = "playwright"

    if not content:
        content = f"[추출 실패 — 수동 처리 필요]\n원본: {url}"
        title = url
        method = "failed"

    path = save_clipping(url, title, content, method, output_dir)
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
