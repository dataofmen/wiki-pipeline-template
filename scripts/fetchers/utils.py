import re
from datetime import datetime
from urllib.parse import urlparse, unquote
from pathlib import Path

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

def determine_source_type(url: str, declared_type: str = "") -> str:
    if declared_type == "thread" or is_twitter_url(url):
        return "thread"
    return declared_type or "url"

def normalize_method(raw_method: str) -> str:
    return METHOD_MAP.get(raw_method, raw_method)

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
    author = ""
    # Clean platform-specific boilerplate before storing
    if is_linkedin_url(url):
        content = _clean_linkedin_content(content)
    if is_twitter_url(url):
        content = _clean_x_content(content)
    # fxtwitter: strip Author/URL/Stats/## Tweet Content metadata and extract author+title
    if method == "fxtwitter" and (source_type == "thread" or is_twitter_url(url)):
        content, author, extracted_title = _clean_fxtwitter_content(content)
        if extracted_title and (not title or title.startswith("Tweet by") or title.startswith("tweet")):
            title = extracted_title
    # Strip Jina metadata headers from all non-thread content
    if source_type != "thread" and not is_twitter_url(url):
        content = _clean_jina_content(content, title)
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
        "author": author,
        "word_count": word_count,
        "fetch_method": method,
        "status": "clipped",
        "domain_hint": domain_hint,
        "tags": tags,
    }
    front_matter = f"---\n{_yaml_dump(front_matter_data)}\n---\n"

    body = f"""{content}

---
*원문: [{url}]({url})*
"""

    path = output_dir / filename
    path.write_text(front_matter + body)
    return path

def _clean_jina_content(content: str, title: str) -> str:
    """Strip Jina AI metadata headers and duplicate H1 from fetched body content."""
    lines = content.splitlines()
    cleaned = []
    skip_next_blank = False
    for line in lines:
        # Strip Jina metadata lines
        if re.match(r"^(URL Source|Published Time|Markdown Content|Number of Pages)\s*:", line):
            skip_next_blank = True
            continue
        if skip_next_blank and line.strip() == "":
            skip_next_blank = False
            continue
        skip_next_blank = False
        cleaned.append(line)

    # Remove leading H1 that duplicates the frontmatter title
    result = "\n".join(cleaned)
    title_core = re.sub(r"[^\w\s]", "", title).strip().lower()
    first_h1 = re.match(r"^#\s+(.+)", result.lstrip(), re.MULTILINE)
    if first_h1:
        h1_core = re.sub(r"[^\w\s]", "", first_h1.group(1)).strip().lower()
        if h1_core == title_core or title_core.startswith(h1_core) or h1_core.startswith(title_core):
            result = result.lstrip()
            result = re.sub(r"^#\s+.+\n*", "", result, count=1)

    return result.strip()

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

def _normalize_url_for_dedup(url: str) -> str:
    """Strip tracking params and trailing slash for duplicate detection."""
    from urllib.parse import urlencode, parse_qsl
    url = url.strip().strip('"').strip("'")
    try:
        p = urlparse(url)
        TRACKING = {"si", "utm_source", "utm_medium", "utm_campaign",
                    "utm_term", "utm_content", "fbclid", "gclid", "ref"}
        qs = [(k, v) for k, v in parse_qsl(p.query) if k not in TRACKING]
        return p._replace(query=urlencode(qs), fragment="").geturl().rstrip("/")
    except Exception:
        return url.rstrip("/")

def find_existing_clipping(url: str, output_dir: Path) -> Path | None:
    """Return path to existing clipping with same normalized URL, or None."""
    target = _normalize_url_for_dedup(url)
    for cf in output_dir.glob("*.md"):
        try:
            for line in cf.read_text(errors="ignore").split("\n"):
                if line.startswith("source_url:"):
                    existing = _normalize_url_for_dedup(
                        line.split("source_url:", 1)[1].strip()
                    )
                    if existing == target:
                        return cf
                    break
        except Exception:
            pass
    return None

