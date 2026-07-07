"""
URL 원문 추출 스크립트
사용법: python3 fetch_url.py "https://..." [--js]
"""
import sys
import re
import argparse
import subprocess
import unicodedata
import yaml
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import unicodedata


def slug_from_title(title: str) -> str:
    title = re.sub(r"[^\w\s-]", "", title.lower())
    words = title.split()[:5]
    slug = "-".join(words)
    return sanitize_slug(slug)


def sanitize_slug(slug: str) -> str:
    """Transliterate Korean/Unicode characters to ASCII-safe slug."""
    normalized = unicodedata.normalize("NFKD", slug)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    ascii_only = re.sub(r"[^\w\s-]", "", ascii_only)
    ascii_only = re.sub(r"[\s_]+", "-", ascii_only)
    ascii_only = re.sub(r"-{2,}", "-", ascii_only)
    return ascii_only.strip("-").lower()


def fetch_via_jina(url: str):
    try:
        import requests
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=30,
                            headers={"Accept": "text/markdown"})
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text
    except Exception as e:
        print(f"jina fetch failed: {e}", file=sys.stderr)
    return None


def fetch_via_readability(url: str):
    """@mozilla/readability + turndown via Bun — Firefox Reader View 수준 본문 추출."""
    import json
    import shutil
    bun = shutil.which("bun") or "/Users/hmkwon/.bun/bin/bun"
    script = Path(__file__).parent / "readability_fetch.mjs"
    try:
        proc = subprocess.run(
            [bun, str(script), url],
            capture_output=True, text=True, timeout=30,
        )
        if proc.stdout.strip():
            result = json.loads(proc.stdout.strip())
            if result.get("content") and not result.get("error"):
                return result["title"], result["content"]
            if result.get("error"):
                print(f"readability-js: {result['error']}", file=sys.stderr)
    except Exception as e:
        print(f"readability-js 실패: {e}", file=sys.stderr)
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
    # Clean title prefix
    title = re.sub(r"^[Tt]itle:\s*", "", title).strip()
    from fetchers.utils import extract_first_sentence, truncate_title_by_language
    title = truncate_title_by_language(extract_first_sentence(title))
    date = datetime.now().strftime("%Y%m%d")
    slug = sanitize_slug(slug_from_title(title) or urlparse(url).netloc.replace(".", "-"))
    slug = slug[:20].strip("-")
    filename = f"{date}-{slug}.md"
    domain_hint = detect_domain_hint(url, title, content)
    word_count = len(content.split())

    front_matter_data = {
        "source_url": url,
        "source_type": "url",
        "clipped_at": datetime.now().isoformat(),
        "title": title,
        "author": "",
        "word_count": word_count,
        "fetch_method": method,
        "status": "clipped",
        "domain_hint": domain_hint,
        "tags": [],
    }
    front_matter = f"---\n{yaml.dump(front_matter_data, allow_unicode=True, default_flow_style=False).strip()}\n---\n\n{content}\n\n---\n*원문: [{url}]({url})*\n"
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
