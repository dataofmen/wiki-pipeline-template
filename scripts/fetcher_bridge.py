"""
Fetcher Bridge — wraps ultimate-fetcher for the wiki pipeline.

Uses subprocess with ultimate-fetcher's venv Python for reliable execution,
since the system Python cannot import ultimate_fetcher directly.

When called as CLI under ultimate-fetcher's venv Python, runs the fetch
directly via asyncio. When called as a module (from pipeline.py), delegates
to the venv Python subprocess.

Usage:
  # As Python module (from pipeline.py):
  from fetcher_bridge import fetch_and_save
  result = fetch_and_save("https://example.com", Path("clippings"))

  # As CLI (under ultimate-fetcher venv Python):
  /path/to/ultimate-fetcher/.venv/bin/python3 fetcher_bridge.py "https://example.com" --output clippings/
"""

import asyncio
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import urlopen

from fetchers.youtube import fetch_youtube_transcript, is_youtube_video_url
from fetchers.pdf import fetch_pdf_via_marker, is_pdf_url
from fetchers.twitter import fetch_via_fxtwitter, is_twitter_url
from fetchers.readability import fetch_via_readability_js
from fetchers.utils import format_clipping, determine_source_type, _normalize_url_for_dedup, find_existing_clipping

# yt-dlp binary (search PATH then common Homebrew location)
YT_DLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"





UF_VENV_PYTHON = str(Path.home() / "Projects" / "ultimate-fetcher" / ".venv" / "bin" / "python3")
































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

    # Strip common title prefixes from fetcher output
    title = re.sub(r"^Title:\s*", "", title).strip()
    # Strip trailing " / X" noise from X.com page titles
    title = re.sub(r'\s*/\s*[Xx]\s*$', '', title).strip()
    # X.com titles: "Author on X: "tweet content"" → extract tweet content
    x_title_match = re.match(r'.+?\s+on\s+[Xx]:\s*"(.+?)"?\s*$', title)
    if x_title_match:
        title = x_title_match.group(1).strip().strip('"').strip()
    elif re.search(r'\s+on\s+[Xx]:\s+', title):
        # No quotes: take everything after "on X: "
        title = re.sub(r'^.+?\s+on\s+[Xx]:\s+', '', title).strip()
    # PDF: prefer content-derived title over generic filename
    if re.match(r'^[A-Za-z0-9_ -]+\.pdf$', title, re.IGNORECASE):
        # Try to extract title from content (first H1 or H2)
        for line in content.splitlines():
            stripped = line.lstrip("# ").strip()
            if stripped and len(stripped) > 10 and not stripped.startswith("http"):
                title = stripped
                break
        else:
            url_path = urlparse(url).path.strip("/").replace("-", " ").replace(".pdf", "").strip()
            if url_path:
                title = url_path

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










def _run_as_subprocess(url: str, output_dir: Path, source_type: str, python_bin: str) -> dict:
    """Run fetcher_bridge.py as subprocess using ultimate-fetcher's venv Python."""
    script_path = Path(__file__)
    cmd = [
        python_bin,
        str(script_path),
        url,
        "--output", str(output_dir),
    ]
    if source_type:
        cmd.extend(["--type", source_type])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        error_content = f"[추출 실패 — 타임아웃]\n원본: {url}"
        return {
            "source_url": url,
            "clipping_path": "",
            "status": "error",
            "domain_hint": "general",
            "fetch_method": "failed",
            "error": error_content
        }

    # Parse JSON output from subprocess
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    if stdout:
        # Try to parse JSON from stdout
        for line in stdout.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    result = json.loads(line)
                    if "source_url" in result:
                        return result
                except json.JSONDecodeError:
                    continue

        # Fallback: look for "Saved:" line (old format)
        for line in stdout.split("\n"):
            if line.startswith("Saved:"):
                clipping_path = line.split(":", 1)[1].strip()
                return {
                    "source_url": url,
                    "clipping_path": clipping_path,
                    "status": "success" if proc.returncode == 0 else "error",
                    "domain_hint": "general",
                    "fetch_method": "subprocess",
                }

    # No useful output — error case
    error_detail = stderr[:500] if stderr else f"exit code {proc.returncode}"
    error_content = f"[추출 실패 — 수동 처리 필요]\n원본: {url}\n오류: {error_detail}"
    return {
        "source_url": url,
        "clipping_path": "",
        "status": "error",
        "domain_hint": "general",
        "fetch_method": "failed",
        "error": error_content
    }






def fetch_and_save(url: str, output_dir: Path, source_type: str = "",
                   python_bin: str = "") -> dict:
    """Sync entry point. Fetches via ultimate-fetcher and saves clipping file.

    Uses subprocess with ultimate-fetcher's venv Python when direct import
    isn't available (the common case when running under system Python).

    Returns dict with: source_url, clipping_path, status, domain_hint, fetch_method
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Dedup check: return existing clipping if same URL was already clipped
    existing = find_existing_clipping(url, output_dir)
    if existing:
        import logging as _log
        _log.getLogger(__name__).info(f"Dedup: reusing existing clipping {existing.name} for {url}")
        return {
            "source_url": url,
            "clipping_path": str(existing),
            "status": "duplicate",
            "domain_hint": "general",
            "fetch_method": "dedup",
        }

    source_type = determine_source_type(url, source_type)

    # X.com/Twitter: fxtwitter API 직접 호출 (readability-js 폴백 전에 처리)
    if is_twitter_url(url):
        fx = fetch_via_fxtwitter(url)
        if fx.get("content"):
            path = format_clipping(
                url, fx["title"], fx["content"],
                fx["method"], "thread",
                fx["domain_hint"], output_dir,
            )
            # author 필드 직접 업데이트
            if fx.get("author"):
                import re as _re
                text = path.read_text(encoding="utf-8")
                text = _re.sub(r'^author: .*$', f'author: {fx["author"]}', text, flags=_re.MULTILINE)
                path.write_text(text, encoding="utf-8")
            return {
                "source_url": url,
                "clipping_path": str(path),
                "status": "success",
                "domain_hint": fx["domain_hint"],
                "fetch_method": fx["method"],
            }
        # fxtwitter 실패 시 ultimate-fetcher/readability-js 폴백 허용

    # PDF: use Marker for high-quality conversion (preserves tables, images, equations)
    if is_pdf_url(url):
        result = fetch_pdf_via_marker(url, output_dir=output_dir)
        if result.get("content") and not result.get("error"):
            path = format_clipping(
                url, result["title"], result["content"],
                result["method"], source_type or "url",
                result["domain_hint"], output_dir,
            )
            return {
                "source_url": url,
                "clipping_path": str(path),
                "status": "success",
                "domain_hint": result["domain_hint"],
                "fetch_method": result["method"],
            }
        # Marker failed — fall through to general fetcher

    # YouTube video: use yt-dlp transcript (community posts / channels skip this)
    if is_youtube_video_url(url) and Path(YT_DLP).exists():
        result = fetch_youtube_transcript(url)
        if result.get("content") and not result.get("error"):
            path = format_clipping(
                url, result["title"], result["content"],
                result["method"], source_type or "video",
                result["domain_hint"], output_dir,
            )
            return {
                "source_url": url,
                "clipping_path": str(path),
                "status": "success",
                "domain_hint": result["domain_hint"],
                "fetch_method": result["method"],
            }
        # yt-dlp failed — fall through to general fetcher

    # Try direct async call first (works when running under UF venv Python)
    can_import = False
    try:
        from ultimate_fetcher.core.router import route_request  # noqa: F401
        can_import = True
    except ImportError:
        pass

    if can_import:
        try:
            result = asyncio.run(_fetch_via_ultimate_fetcher(url))
            if result.get("error") and not result.get("content"):
                error_content = f"[추출 실패 — 수동 처리 필요]\n원본: {url}"
                if result.get("error"):
                    error_content += f"\n오류: {result['error']}"
                return {
                    "source_url": url,
                    "clipping_path": "",
                    "status": "error",
                    "domain_hint": "general",
                    "fetch_method": "failed",
                    "error": error_content
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
        except Exception as e:
            error_content = f"[추출 실패 — 수동 처리 필요]\n원본: {url}\n오류: {e}"
            return {
                "source_url": url,
                "clipping_path": "",
                "status": "error",
                "domain_hint": "general",
                "fetch_method": "failed",
                "error": error_content
            }

    # ultimate-fetcher 직접 임포트 불가 → Readability.js 폴백 시도
    rjs = fetch_via_readability_js(url)
    if rjs.get("content"):
        path = format_clipping(
            url, rjs["title"], rjs["content"],
            rjs["method"], source_type or "url",
            rjs["domain_hint"], output_dir,
        )
        return {
            "source_url": url,
            "clipping_path": str(path),
            "status": "success",
            "domain_hint": rjs["domain_hint"],
            "fetch_method": rjs["method"],
        }

    # 최후 수단 — subprocess로 ultimate-fetcher venv Python 호출
    uf_python = python_bin or UF_VENV_PYTHON
    if not Path(uf_python).exists():
        uf_python = "python3"

    return _run_as_subprocess(url, output_dir, source_type, uf_python)


def main():
    parser = argparse.ArgumentParser(description="Fetch and save a URL via ultimate-fetcher")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--output", default=None, help="Output directory (default: ../clippings)")
    parser.add_argument("--type", default="", help="Source type hint: url, thread, pdf")
    args = parser.parse_args()

    output_dir = Path(args.output) if args.output else Path(__file__).parent.parent / "clippings"
    result = fetch_and_save(args.url, output_dir, source_type=args.type)

    # Output as JSON for pipeline.py to parse
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
