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

# yt-dlp binary (search PATH then common Homebrew location)
YT_DLP = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"


def _yaml_dump(data: dict) -> str:
    """Minimal YAML serializer for frontmatter dicts (no pyyaml dependency)."""
    lines = []
    for key, value in data.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: []")
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        elif isinstance(value, str):
            # Quote strings with YAML-special chars or leading/trailing whitespace
            needs_quote = any(c in value for c in [':', '#', '[', ']', '{', '}', '&', '*', '!', '|', '>']) or \
                          value.startswith(("'", '"', '-', '?', '@', '`')) or not value
            if needs_quote:
                escaped = value.replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            else:
                lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)

UF_VENV_PYTHON = str(Path("/Users/hmkwon/Projects/ultimate-fetcher/.venv/bin/python3"))


def detect_domain_hint(url: str, title: str, content: str) -> str:
    text = f"{url} {title} {content[:500]}".lower()
    if any(k in text for k in ["trading", "invest", "stock", "crypto", "quant", "backtest", "sharpe"]):
        return "trading"
    if any(k in text for k in ["ux", "hci", "usability", "user research", "product", "design", "figma"]):
        return "ux"
    if any(k in text for k in ["note", "knowledge", "pkm", "zettelkasten", "philosophy", "essay", "thinking"]):
        return "pkm"
    return "general"


def slug_from_title_and_url(title: str, url: str) -> str:
    """Generate an ASCII slug from title, falling back to URL-based unique ID."""
    # Try title-based slug — keep only ASCII word chars after NFKD
    import unicodedata as _ud
    normalized = _ud.normalize("NFKD", title.lower())
    ascii_title = "".join(c for c in normalized if ord(c) < 128 and not _ud.combining(c))
    clean = re.sub(r"[^\w\s-]", "", ascii_title).strip()
    if len(clean) > 5:
        words = clean.split()[:5]
        return "-".join(words)

    # For YouTube, use video ID
    yt_match = re.search(r"[?&]v=([A-Za-z0-9_-]+)", url) or re.search(r"youtu\.be/([A-Za-z0-9_-]+)", url)
    if yt_match:
        return f"yt-{yt_match.group(1)}"

    # For X.com/Twitter threads, extract tweet ID from URL
    twitter_match = re.search(r"/status/(\d+)", url)
    if twitter_match:
        return f"thread-{twitter_match.group(1)}"

    # For other URLs with path segments, use last path part
    path = urlparse(url).path.strip("/")
    if path:
        last_segment = path.split("/")[-1]
        clean_segment = re.sub(r"[^\w-]", "", last_segment)[:60]
        if len(clean_segment) > 3:
            return clean_segment

    return "untitled"


def is_twitter_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return "twitter.com" in domain or "x.com" in domain


def is_youtube_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return "youtube.com" in domain or "youtu.be" in domain


def _vtt_to_text(vtt_content: str) -> str:
    """Convert VTT subtitle content to clean, deduplicated transcript text."""
    lines = []
    seen: set[str] = set()
    for line in vtt_content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"\d{2}:\d{2}:\d{2}", line) or re.match(r"^\d+$", line):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        clean = re.sub(r"&gt;", ">", clean)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        lines.append(clean)
    return "\n".join(lines)


def fetch_youtube_transcript(url: str) -> dict:
    """Fetch YouTube video info + transcript via yt-dlp. Returns title/content dict."""
    if not Path(YT_DLP).exists():
        return {"error": f"yt-dlp not found at {YT_DLP}", "content": "", "title": ""}

    with tempfile.TemporaryDirectory() as tmpdir:
        out_tmpl = os.path.join(tmpdir, "%(id)s")

        # Step 1: Get metadata as JSON
        meta_cmd = [
            YT_DLP, "--dump-json", "--no-playlist",
            "--quiet", "--no-warnings", url,
        ]
        meta_proc = subprocess.run(meta_cmd, capture_output=True, text=True, timeout=60)
        title = urlparse(url).netloc
        duration_str = ""
        channel = ""
        if meta_proc.returncode == 0 and meta_proc.stdout.strip():
            try:
                meta = json.loads(meta_proc.stdout.strip())
                title = meta.get("title", title)
                channel = meta.get("channel", meta.get("uploader", ""))
                secs = meta.get("duration", 0)
                duration_str = f"{secs // 60}분 {secs % 60}초" if secs else ""
            except json.JSONDecodeError:
                pass

        # Step 2: Download Korean subtitles (auto-generated), fallback to English
        for lang in ("ko", "en"):
            sub_cmd = [
                YT_DLP,
                "--write-auto-subs", "--sub-langs", lang,
                "--sub-format", "vtt",
                "--skip-download", "--no-playlist",
                "--quiet", "--no-warnings",
                "-o", out_tmpl, url,
            ]
            subprocess.run(sub_cmd, capture_output=True, timeout=120)
            # Find downloaded .vtt file
            vtt_files = list(Path(tmpdir).glob(f"*.{lang}.vtt"))
            if vtt_files:
                vtt_text = _vtt_to_text(vtt_files[0].read_text(encoding="utf-8", errors="replace"))
                if len(vtt_text) > 100:
                    header = f"**채널**: {channel}" if channel else ""
                    if duration_str:
                        header += f" | **길이**: {duration_str}" if header else f"**길이**: {duration_str}"
                    content = f"{header}\n\n{vtt_text}" if header else vtt_text
                    return {
                        "title": title,
                        "content": content,
                        "method": "yt-dlp-transcript",
                        "url": url,
                        "domain_hint": detect_domain_hint(url, title, vtt_text),
                    }

    return {
        "error": "자막 없음 — 자막이 제공되지 않는 영상",
        "title": title,
        "content": f"[자막 없음]\n채널: {channel}\n원본: {url}",
        "method": "yt-dlp-no-subs",
        "url": url,
        "domain_hint": "general",
    }


def is_reddit_url(url: str) -> bool:
    return "reddit.com" in urlparse(url).netloc.lower()


def is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in urlparse(url).netloc.lower()


def _clean_linkedin_content(content: str) -> str:
    """Strip LinkedIn navigation noise, keeping only the post body text and article link."""
    from urllib.parse import parse_qs, urlparse as _up

    def _unwrap_redir(href: str) -> str:
        """Unwrap linkedin.com/redir/redirect?url=... → actual URL."""
        if "linkedin.com/redir/redirect" in href:
            try:
                qs = parse_qs(_up(href).query)
                return unquote(qs.get("url", [href])[0])
            except Exception:
                pass
        return href

    # Step 1: globally replace all LinkedIn redirect URLs before per-line processing
    def _replace_redir_url(m):
        return _unwrap_redir(m.group(0))
    content = re.sub(
        r'https://(?:www\.)?linkedin\.com/redir/redirect\?url=[^\s\)"\']+',
        _replace_redir_url, content
    )

    # Step 2: strip title attributes: (url "title") → (url)
    content = re.sub(r'\(([^\s\)]+)\s+"[^"]*"\)', r'(\1)', content)

    # Step 3: convert article preview images [![alt]()](url) → [alt](url)
    # Also strip nested brackets from alt text to avoid wikilink conflicts
    def _fix_preview(m):
        alt = re.sub(r'[\[\]]', '', m.group(1)).strip()  # remove [ ] from alt text
        return f"[{alt}]({m.group(2)})" if alt else ""
    content = re.sub(
        r'\[!\[([^\]]*(?:\[[^\]]*\][^\]]*)*)\]\([^\)]*\)\]\(([^\)]+)\)',
        _fix_preview,
        content
    )
    # Step 4: remove dangling image/noise lines like `![` with no closing `]()`
    content = re.sub(r'^!\[$', '', content, flags=re.MULTILINE)

    stop_keywords = (
        "trk=public_post_like-cta",
        "trk=public_post_comment-cta",
        "trk=public_post_social-actions",
        "trk=public_post_reshare-cta",
    )

    lines_out = []
    for line in content.splitlines():
        stripped = line.strip()

        # Truncate at engagement bar (Like / Comment / Share CTAs)
        if any(kw in stripped for kw in stop_keywords):
            break

        # Skip navigation / UI noise lines
        if any(pat in stripped for pat in (
            "Report this post", "Report this comment",
            "trk=public_post_feed-actor",
            "/uas/login?session_redirect",
        )):
            continue
        if re.match(r'^\* \[', stripped) and "/uas/login" in stripped:
            continue
        if re.match(r'^\d+[hdw]$', stripped):  # "23h", "2d", "1w"
            continue
        # Bare `!` lines or empty images
        if re.match(r'^!+$', stripped) or re.match(r'^!\[[\s!]*\]\(\s*\)$', stripped):
            continue
        # LinkedIn signup links for hashtags → plain text
        line = re.sub(
            r'\[([^\]]+)\]\(https://[^\s\)]*linkedin\.com/signup[^\)]*\)',
            lambda m: m.group(1) if m.group(1).startswith("#") else m.group(0),
            line
        )

        lines_out.append(line)

    joined = "\n".join(lines_out)
    # Collapse 3+ blank lines to 2
    joined = re.sub(r'\n{3,}', '\n\n', joined)
    # Ensure paragraph breaks: insert blank line between any two adjacent non-empty lines
    result_lines = joined.split('\n')
    final_lines = []
    for i, line in enumerate(result_lines):
        final_lines.append(line)
        if line.strip() and i + 1 < len(result_lines) and result_lines[i + 1].strip():
            final_lines.append('')
    return '\n'.join(final_lines).strip()


def _clean_x_content(content: str) -> str:
    """Strip X.com/Twitter boilerplate: login wall, JS warnings, nav chrome."""
    x_noise_patterns = [
        r"^# JavaScript is not available\.",
        r"^We['']ve detected that JavaScript is disabled",
        r"^\[Help Center\]",
        r"^\[Terms of Service\]",
        r"^\[Privacy Policy\]",
        r"^\[Cookie Policy\]",
        r"^\[Imprint\]",
        r"^\[Ads info\]",
        r"^© \d{4} X Corp\.",
        r"^Don't miss what's happening\.",
        r"^People on X are the first to know\.",
        r"^\[Log in\]",
        r"^\[Sign up\]",
        r"^See new posts$",
        r"^# $",  # empty heading
        r"^\[!\[Image \d+\]\(https://pbs\.twimg\.com/profile_images",
    ]
    stop_markers = (
        "trk=public_post_like-cta",
        "Log in to your account",
    )

    lines_out = []
    for line in content.splitlines():
        stripped = line.strip()

        # Stop at engagement/CTA markers
        if any(m in stripped for m in stop_markers):
            break

        # Skip known noise patterns
        if any(re.match(pat, stripped) for pat in x_noise_patterns):
            continue

        # Skip lines that are just X navigation URLs
        if re.match(r'^\[[^\]]*\]\(https://(?:x\.com|twitter\.com)/(?:tos|privacy|login|signup|help|i/flow)', stripped):
            continue

        lines_out.append(line)

    joined = "\n".join(lines_out)
    # Collapse 3+ blank lines to 2
    joined = re.sub(r'\n{3,}', '\n\n', joined)
    # Ensure paragraph breaks between adjacent non-empty lines
    result_lines = joined.split('\n')
    final_lines = []
    for i, line in enumerate(result_lines):
        final_lines.append(line)
        if line.strip() and i + 1 < len(result_lines) and result_lines[i + 1].strip():
            final_lines.append('')
    return '\n'.join(final_lines).strip()


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

    # Strip common title prefixes from fetcher output
    title = re.sub(r"^Title:\s*", "", title).strip()
    # X.com titles: "Nainsi Dwivedi on X: \"...\" / X" → extract quoted part
    x_title_match = re.match(r'(.+?)\s+on\s+[Xx]:\s*"(.+?)"', title)
    if x_title_match:
        title = x_title_match.group(2)
    # PDF titles like "A.pdf" → use URL path instead
    if re.match(r'^[A-Za-z0-9_ -]+\.pdf$', title, re.IGNORECASE) and len(title) < 30:
        url_path = urlparse(url).path.strip("/")
        if url_path:
            title = url_path.replace("-", " ").replace(".pdf", "").strip()

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


def sanitize_slug(slug: str) -> str:
    """Produce an ASCII-only URL slug. Korean/CJK characters are dropped."""
    # NFKD separates accented Latin base chars from combining marks
    normalized = unicodedata.normalize("NFKD", slug)
    # Remove combining marks (accents, etc.) and keep only ASCII printable
    ascii_only = "".join(c for c in normalized if ord(c) < 128 and not unicodedata.combining(c))
    # Keep only word chars, hyphens, and spaces
    ascii_only = re.sub(r"[^\w\s-]", "", ascii_only)
    # Collapse whitespace/underscores to hyphens
    ascii_only = re.sub(r"[\s_]+", "-", ascii_only)
    ascii_only = re.sub(r"-{2,}", "-", ascii_only)
    return ascii_only.strip("-").lower()


def format_clipping(url: str, title: str, content: str, method: str,
                    source_type: str, domain_hint: str, output_dir: Path) -> Path:
    """Write a clipping .md file with YAML front matter."""
    # Decode percent-encoded URLs (e.g. Korean slugs) for readability
    url = unquote(url)
    # Clean platform-specific boilerplate before storing
    if is_linkedin_url(url):
        content = _clean_linkedin_content(content)
    if is_twitter_url(url):
        content = _clean_x_content(content)
    date = datetime.now().strftime("%Y%m%d")
    slug = sanitize_slug(slug_from_title_and_url(title, url) or urlparse(url).netloc.replace(".", "-"))
    filename = f"{date}-{slug}.md"
    word_count = len(content.split())

    tags = []
    if source_type == "thread" or is_twitter_url(url):
        tags = ["thread", "x.com"]
    elif is_youtube_url(url):
        tags = ["video", "youtube"]
    elif is_reddit_url(url):
        tags = ["reddit"]

    front_matter_data = {
        "source_url": url,
        "source_type": source_type,
        "clipped_at": datetime.now().isoformat(),
        "title": title,
        "author": "",
        "word_count": word_count,
        "fetch_method": method,
        "status": "clipped",
        "domain_hint": domain_hint,
        "tags": tags,
    }
    front_matter = f"---\n{_yaml_dump(front_matter_data)}\n---\n"

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
        path = format_clipping(url, urlparse(url).netloc, error_content, "failed",
                               source_type or "url", "general", output_dir)
        return {
            "source_url": url,
            "clipping_path": str(path),
            "status": "error",
            "domain_hint": "general",
            "fetch_method": "failed",
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
    path = format_clipping(url, urlparse(url).netloc, error_content, "failed",
                           source_type or "url", "general", output_dir)
    return {
        "source_url": url,
        "clipping_path": str(path),
        "status": "error",
        "domain_hint": "general",
        "fetch_method": "failed",
    }


def fetch_and_save(url: str, output_dir: Path, source_type: str = "",
                   python_bin: str = "") -> dict:
    """Sync entry point. Fetches via ultimate-fetcher and saves clipping file.

    Uses subprocess with ultimate-fetcher's venv Python when direct import
    isn't available (the common case when running under system Python).

    Returns dict with: source_url, clipping_path, status, domain_hint, fetch_method
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    source_type = determine_source_type(url, source_type)

    # YouTube: use yt-dlp transcript directly (more useful than metadata scraping)
    if is_youtube_url(url) and Path(YT_DLP).exists():
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

    # Direct import unavailable — use subprocess with ultimate-fetcher's venv Python
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