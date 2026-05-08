"""
Fetcher Bridge — wraps ultimate-fetcher for the wiki pipeline.

Provides sync interface to the async ultimate-fetcher, formats results
into the existing clipping file format (YAML front matter + Markdown),
and handles domain hint detection, slug generation, and file output.

Usage:
  # As Python module:
  from fetcher_bridge import fetch_and_save
  result = fetch_and_save("https://example.com", Path("clippings"))

  # As CLI:
  python3 fetcher_bridge.py "https://example.com" --output clippings/
"""

import asyncio
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def detect_domain_hint(url: str, title: str, content: str) -> str:
    text = f"{url} {title} {content[:500]}".lower()
    if any(k in text for k in ["trading", "invest", "stock", "crypto", "quant", "backtest", "sharpe"]):
        return "trading"
    if any(k in text for k in ["ux", "hci", "usability", "user research", "product", "design", "figma"]):
        return "ux"
    if any(k in text for k in ["note", "knowledge", "pkm", "zettelkasten", "philosophy", "essay", "thinking"]):
        return "pkm"
    return "general"


def slug_from_title(title: str) -> str:
    title = re.sub(r"[^\w\s-]", "", title.lower())
    words = title.split()[:5]
    return "-".join(words) if words else "untitled"


def is_twitter_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return "twitter.com" in domain or "x.com" in domain


def is_youtube_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return "youtube.com" in domain or "youtu.be" in domain


def is_reddit_url(url: str) -> bool:
    return "reddit.com" in urlparse(url).netloc.lower()


def determine_source_type(url: str, declared_type: str = "") -> str:
    if declared_type == "thread" or is_twitter_url(url):
        return "thread"
    return declared_type or "url"


METHOD_MAP = {
    "jina": "jina",
    "curl_cffi/chrome": "curl_cffi",
    "curl_cffi/safari": "curl_cffi",
    "curl_cffi/safari_ios": "curl_cffi",
    "playwright": "playwright",
    "twitter_fxtwitter": "fxtwitter",
    "twitter_oembed": "oembed",
    "twitter_nitter": "nitter",
    "youtube_yt-dlp": "yt-dlp",
    "reddit_json_api": "reddit-api",
    "github_gh_cli": "gh-cli",
    "hackernews_firebase": "hn-api",
    "rss_feedparser": "rss",
}


def normalize_method(raw_method: str) -> str:
    return METHOD_MAP.get(raw_method, raw_method)


async def _fetch_via_ultimate_fetcher(url: str) -> dict:
    """Call ultimate_fetcher.route_request and normalize the result."""
    try:
        from ultimate_fetcher.core.router import route_request
    except ImportError:
        return {"error": "ultimate_fetcher not installed", "markdown": "", "url": url}

    result = await route_request(url)

    if result.get("error") and not result.get("markdown"):
        return {
            "error": result["error"],
            "markdown": "",
            "url": url,
            "method": "failed",
        }

    # Twitter plugin may signal fallback_to_scheduler
    if result.get("fallback_to_scheduler"):
        # The router already handles this by calling adaptive_fetch,
        # but if the result still has the flag, try the url directly
        from ultimate_fetcher.core.scheduler import adaptive_fetch
        fallback_result = await adaptive_fetch(result.get("url", url))
        if fallback_result:
            result = fallback_result

    title = result.get("title", "")
    content = result.get("markdown", "") or result.get("text", "")
    method = normalize_method(result.get("method", "unknown"))

    # Improve generic titles from Jina results
    if method == "jina" and (not title or title.startswith("Fetched via")):
        lines = content.splitlines()
        for line in lines:
            stripped = line.lstrip("# ").strip()
            if stripped and len(stripped) > 5:
                title = stripped
                break

    return {
        "title": title or urlparse(url).netloc,
        "content": content,
        "method": method,
        "url": url,
        "domain_hint": detect_domain_hint(url, title, content),
    }


def format_clipping(url: str, title: str, content: str, method: str,
                    source_type: str, domain_hint: str, output_dir: Path) -> Path:
    """Write a clipping .md file with YAML front matter."""
    date = datetime.now().strftime("%Y%m%d")
    slug = slug_from_title(title) or urlparse(url).netloc.replace(".", "-")
    filename = f"{date}-{slug}.md"
    word_count = len(content.split())

    tags = []
    if source_type == "thread" or is_twitter_url(url):
        tags = ["thread", "x.com"]
    elif is_youtube_url(url):
        tags = ["video", "youtube"]
    elif is_reddit_url(url):
        tags = ["reddit"]

    front_matter = f"""---
source_url: {url}
source_type: {source_type}
clipped_at: {datetime.now().isoformat()}
title: {title}
author: ""
word_count: {word_count}
fetch_method: {method}
status: clipped
domain_hint: {domain_hint}
tags: [{', '.join(tags)}]
---
"""

    if source_type == "thread" or is_twitter_url(url):
        body = f"""원본: [{url}]({url})

---

{content}

---
*원문: [{url}]({url})*
"""
    else:
        body = f"""{content}

---
*원문: [{url}]({url})*
"""

    path = output_dir / filename
    path.write_text(front_matter + body)
    return path


def fetch_and_save(url: str, output_dir: Path, source_type: str = "") -> dict:
    """Sync entry point. Fetches via ultimate-fetcher and saves clipping file.

    Returns dict with: source_url, clipping_path, status, domain_hint, fetch_method
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    source_type = determine_source_type(url, source_type)

    try:
        result = asyncio.run(_fetch_via_ultimate_fetcher(url))
    except Exception as e:
        error_content = f"[추출 실패 — 수동 처리 필요]\n원본: {url}\n오류: {e}"
        path = format_clipping(url, urlparse(url).netloc, error_content, "failed",
                               source_type, "general", output_dir)
        return {
            "source_url": url,
            "clipping_path": str(path),
            "status": "error",
            "domain_hint": "general",
            "fetch_method": "failed",
        }

    if result.get("error") and not result.get("content"):
        error_content = f"[추출 실패 — 수동 처리 필요]\n원본: {url}"
        if result.get("error"):
            error_content += f"\n오류: {result['error']}"
        path = format_clipping(url, urlparse(url).netloc, error_content, "failed",
                               source_type, "general", output_dir)
        return {
            "source_url": url,
            "clipping_path": str(path),
            "status": "error",
            "domain_hint": "general",
            "fetch_method": "failed",
        }

    title = result["title"]
    content = result["content"]
    method = result["method"]
    domain_hint = result["domain_hint"]

    path = format_clipping(url, title, content, method, source_type, domain_hint, output_dir)

    return {
        "source_url": url,
        "clipping_path": str(path),
        "status": "success",
        "domain_hint": domain_hint,
        "fetch_method": method,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch and save a URL via ultimate-fetcher")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--output", default=None, help="Output directory (default: ../clippings)")
    parser.add_argument("--type", default="", help="Source type hint: url, thread, pdf")
    args = parser.parse_args()

    output_dir = Path(args.output) if args.output else Path(__file__).parent.parent / "clippings"
    result = fetch_and_save(args.url, output_dir, source_type=args.type)

    if result["status"] == "success":
        print(f"Clipping saved: {result['clipping_path']}")
    else:
        print(f"Saved with errors: {result['clipping_path']}", file=sys.stderr)

    # Also print the path for pipeline.py to parse
    print(f"Saved: {result['clipping_path']}")


if __name__ == "__main__":
    main()