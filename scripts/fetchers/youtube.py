import json
import re
import subprocess
import shutil
from pathlib import Path
YT_DLP = shutil.which('yt-dlp') or '/opt/homebrew/bin/yt-dlp'

def is_youtube_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return "youtube.com" in domain or "youtu.be" in domain

def is_youtube_video_url(url: str) -> bool:
    """Returns True only for actual video URLs (not community posts, channels, etc.)."""
    if not is_youtube_url(url):
        return False
    path = urlparse(url).path
    # Exclude community posts (/post/...), channels, playlists, etc.
    if re.search(r"/post/|/@|/channel/|/c/|/user/|/playlist", path):
        return False
    return bool(re.search(r"[?&]v=", url) or "youtu.be/" in url)

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

