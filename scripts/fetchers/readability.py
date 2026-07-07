import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from fetchers.utils import detect_domain_hint

def fetch_via_readability_js(url: str) -> dict:
    """@mozilla/readability + turndown via Bun — ultimate-fetcher 실패 시 폴백."""
    script = Path(__file__).parent / "readability_fetch.mjs"
    if not script.exists():
        return {}
    bun = shutil.which("bun") or "/Users/hmkwon/.bun/bin/bun"
    try:
        proc = subprocess.run(
            [bun, str(script), url],
            capture_output=True, text=True, timeout=30,
        )
        if proc.stdout.strip():
            result = json.loads(proc.stdout.strip())
            if result.get("content") and not result.get("error"):
                return {
                    "title": result.get("title", "") or urlparse(url).netloc,
                    "content": result["content"],
                    "method": "readability-js",
                    "domain_hint": detect_domain_hint(url, result.get("title", ""), result["content"]),
                }
    except Exception as e:
        print(f"readability-js 폴백 실패: {e}", file=sys.stderr)
    return {}

