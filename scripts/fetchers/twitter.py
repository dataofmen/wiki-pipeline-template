import json
import re
from urllib.request import urlopen
from urllib.parse import urlparse

def is_twitter_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return "twitter.com" in domain or "x.com" in domain

def fetch_via_fxtwitter(url: str) -> dict:
    """X.com/Twitter 전용: api.fxtwitter.com으로 tweet 본문 추출.

    ultimate-fetcher 없이도 X.com 로그인 장벽을 우회한다.
    """
    tweet_match = re.search(r"/status/(\d+)", url)
    if not tweet_match:
        return {}
    tweet_id = tweet_match.group(1)
    api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
    try:
        req = __import__("urllib.request", fromlist=["urlopen"]).urlopen(api_url, timeout=15)
        data = json.loads(req.read().decode("utf-8"))
    except Exception as e:
        print(f"fxtwitter API 실패: {e}", file=sys.stderr)
        return {}

    tweet = data.get("tweet", {})
    if not tweet:
        return {}

    author_name = tweet.get("author", {}).get("name", "")
    author_screen = tweet.get("author", {}).get("screen_name", "")
    text = tweet.get("text", "")
    replies = tweet.get("replies") or []
    article = tweet.get("article") or {}

    # X Article (Note Tweet): text 비어 있고 article.content.blocks 있는 경우
    if not text and article:
        article_title = article.get("title", "")
        blocks = article.get("content", {}).get("blocks", [])
        article_lines = []
        if article_title:
            article_lines.append(f"# {article_title}")
        for block in blocks:
            block_text = block.get("text", "").strip()
            if block_text:
                article_lines.append(block_text)
        text = "\n\n".join(article_lines)
    
    quote = tweet.get("quote") or {}
    if quote:
        quote_text = quote.get("text", "").strip()
        quote_author = quote.get("author", {}).get("name", "")
        if quote_text:
            text = text + f"\n\n[Quote from {quote_author}]:\n{quote_text}"

    # thread 필드가 list인 경우 스레드 본문 조합, 아니면 단일 트윗
    parts = [text]
    if isinstance(replies, list):
        for r in replies:
            r_text = r.get("text", "")
            if r_text and r_text != text:
                parts.append(r_text)
    elif isinstance(tweet.get("thread"), list):
        for r in tweet["thread"]:
            r_text = r.get("text", "")
            if r_text and r_text != text:
                parts.append(r_text)
    full_text = "\n\n".join(p for p in parts if p).strip()

    if not full_text:
        return {}

    # 제목: 첫 비-URL 줄 최대 80자
    title = ""
    for line in full_text.splitlines():
        s = line.strip().lstrip("#")
        if s and not s.startswith("http"):
            title = s[:80].rstrip()
            if len(s) > 80:
                title += "…"
            break
    if author_name:
        title = f"{author_name} — {title}" if title else author_name

    content = full_text + f"\n\n*원문: [{url}]({url})*"
    return {
        "title": title,
        "author": author_name,
        "author_screen": author_screen,
        "content": content,
        "method": "fxtwitter",
        "domain_hint": "general",
    }

def _clean_fxtwitter_content(content: str) -> tuple[str, str, str]:
    """Strip fxtwitter metadata boilerplate. Returns (cleaned_content, author, title).

    fxtwitter returns:
        **Author**: Name (@handle)
        **URL**: https://x.com/...
        **Stats**: N Likes | N Retweets | N Views

        ## Tweet Content

        [actual tweet text]
    """
    author = ""
    title = ""
    lines = content.splitlines()
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Extract author name before stripping
        m = re.match(r'^\*\*Author\*\*:\s*(.+?)\s*\(@\w+\)', stripped)
        if m:
            author = m.group(1).strip()
            content_start = i + 1
            continue
        if re.match(r'^\*\*URL\*\*:', stripped):
            content_start = i + 1
            continue
        if re.match(r'^\*\*Stats\*\*:', stripped):
            content_start = i + 1
            continue
        if re.match(r'^##\s+Tweet Content\s*$', stripped):
            content_start = i + 1
            continue

    cleaned_lines = lines[content_start:]
    # Drop leading blank lines
    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)

    cleaned = "\n".join(cleaned_lines).strip()

    # Extract title: first non-empty, non-URL line
    for line in cleaned_lines:
        s = line.strip()
        if s and not s.startswith("http"):
            title = s.lstrip("#").strip()
            # Truncate at 80 chars for title
            if len(title) > 80:
                title = title[:77].rstrip() + "…"
            break

    return cleaned, author, title

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

