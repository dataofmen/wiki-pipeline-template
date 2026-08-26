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

from content_validation import validate_content


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
    """Jina Reader 추출.

    주의: Jina 는 원본이 500/로그인벽/CAPTCHA 여도 **자기 응답은 200** 으로 주고
    에러 페이지 본문을 실어 보낸다. 따라서 여기서 성공을 단정하지 않고, 본문
    유효성은 호출자(_try_fetch)가 content_validation 으로 판정한다.
    """
    try:
        import requests
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=30,
                            headers={"Accept": "text/markdown"})
        if resp.status_code == 200 and resp.text.strip():
            return resp.text
        if resp.status_code != 200:
            print(f"jina HTTP {resp.status_code}", file=sys.stderr)
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


def save_clipping(url: str, title: str, content: str, method: str, output_dir: Path,
                  validation=None) -> Path:
    # Clean title prefix
    title = re.sub(r"^[Tt]itle:\s*", "", title).strip()
    from fetchers.utils import extract_first_sentence, truncate_title_by_language
    title = truncate_title_by_language(extract_first_sentence(title))
    date = datetime.now().strftime("%Y%m%d")
    slug = sanitize_slug(slug_from_title(title) or urlparse(url).netloc.replace(".", "-"))
    slug = slug[:20].strip("-")
    filename = f"{date}-{slug}.md"
    domain_hint = detect_domain_hint(url, title, content)

    if validation is None:
        validation = validate_content(content, source_type="url", url=url, title=title)

    front_matter_data = {
        "source_url": url,
        "source_type": "url",
        "clipped_at": datetime.now().isoformat(),
        "title": title,
        "author": "",
        "word_count": validation.word_count,
        "fetch_method": method,
        # VERIFY 게이트: 본문이 아닌 것은 clipped 로 두지 않는다. fetch_failed 는
        # 노트 생성 대상에서 제외되고 재추출 후보로 남는다.
        "status": "fetch_failed" if validation.failed else "clipped",
        "domain_hint": domain_hint,
        "tags": [],
    }
    if not validation.ok:
        front_matter_data["content_warning"] = validation.code
        front_matter_data["content_warning_reason"] = validation.reason
    front_matter = f"---\n{yaml.dump(front_matter_data, allow_unicode=True, default_flow_style=False).strip()}\n---\n\n{content}\n\n---\n*원문: [{url}]({url})*\n"
    path = output_dir / filename
    path.write_text(front_matter)
    return path


def _jina_attempt(url: str):
    """Jina 결과를 (title, content) 로 정규화."""
    raw = fetch_via_jina(url)
    if not raw:
        return None
    lines = raw.splitlines()
    raw_title = lines[0].lstrip("# ").strip() if lines else url
    return re.sub(r"^Title:\s*", "", raw_title).strip(), raw


def fetch_with_validation(url: str, use_js: bool = False):
    """추출기를 순서대로 시도하고 **본문 유효성 검증을 통과한 첫 결과**를 쓴다.

    기존 구현은 추출기가 무언가를 반환하기만 하면 성공으로 보고 폴백을 멈췄다.
    Jina 는 에러 페이지도 200 으로 반환하므로 폴백이 사실상 죽어 있었고, Medium
    500 페이지가 클리핑되어 환각 노트 5개를 유발했다. 이제는 검증에 실패하면
    다음 추출기로 넘어가고, 전부 실패하면 가장 나은 후보를 fetch_failed 로 남긴다.

    Returns: (title, content, method, ValidationResult)
    """
    attempts = []
    if not use_js:
        attempts.append(("jina", lambda: _jina_attempt(url)))
        attempts.append(("readability", lambda: fetch_via_readability(url)))
    attempts.append(("playwright", lambda: fetch_via_playwright(url)))

    best = None  # 전부 실패했을 때 남길 최선의 후보 (단어수 기준)
    for method, fn in attempts:
        try:
            result = fn()
        except Exception as e:
            print(f"{method} 예외: {e}", file=sys.stderr)
            continue
        if not result:
            continue
        title, content = result
        v = validate_content(content, source_type="url", url=url, title=title)
        if v.usable:
            if not v.ok:
                print(f"{method}: {v}", file=sys.stderr)
            return title, content, method, v
        print(f"{method} 검증 실패 → 폴백: {v}", file=sys.stderr)
        if best is None or v.word_count > best[3].word_count:
            best = (title, content, method, v)

    if best:
        return best
    v = validate_content("", source_type="url", url=url)
    return url, f"[추출 실패 — 수동 처리 필요]\n원본: {url}", "failed", v


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--js", action="store_true", help="JavaScript 렌더링 사용")
    parser.add_argument("--output", default=None, help="저장 디렉토리")
    args = parser.parse_args()

    output_dir = Path(args.output) if args.output else Path(__file__).parent.parent / "clippings"
    output_dir.mkdir(exist_ok=True)

    url = args.url
    title, content, method, validation = fetch_with_validation(url, use_js=args.js)

    path = save_clipping(url, title, content, method, output_dir, validation=validation)
    print(f"저장: {path}")
    if validation.failed:
        print(f"경고: 본문 검증 실패 ({validation}) → status: fetch_failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
