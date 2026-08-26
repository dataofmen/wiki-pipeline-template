"""
X.com / Twitter 스레드 추출 스크립트
사용법: python3 fetch_thread.py "https://x.com/user/status/..." [--output clippings/]
"""
import sys
import re
import json
import argparse
import yaml
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from content_validation import validate_content


def slug_from_url(url: str) -> str:
    # tweet ID를 슬러그로 사용
    match = re.search(r"/status/(\d+)", url)
    if match:
        return f"thread-{match.group(1)}"
    return "thread-unknown"


def extract_tweet_id(url: str) :
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None


def fetch_via_nitter(url: str) :
    """Nitter public mirror로 스레드 추출."""
    try:
        import requests
        from bs4 import BeautifulSoup

        # x.com → nitter.net 변환
        nitter_url = url.replace("x.com", "nitter.net").replace("twitter.com", "nitter.net")
        resp = requests.get(nitter_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        tweets = soup.select(".timeline-item .tweet-content")
        if not tweets:
            return None

        lines = []
        for i, tweet in enumerate(tweets):
            text = tweet.get_text(separator="\n").strip()
            lines.append(f"**트윗 {i+1}:**\n{text}\n")
        return "\n---\n".join(lines)
    except Exception as e:
        print(f"nitter 실패: {e}", file=sys.stderr)
    return None


def fetch_via_vxtwitter(url: str) :
    """vxtwitter API로 트윗 추출 (fallback 1)."""
    try:
        import requests
        tweet_id = extract_tweet_id(url)
        if not tweet_id:
            return None
        api_url = f"https://api.vxtwitter.com/twitter/status/{tweet_id}"
        resp = requests.get(api_url, timeout=20,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("text", "")
            if text:
                # 트윗 내용 포맷팅
                lines = [f"**트윗 1:**\n{text}\n"]
                # 스레드의 경우 replies도 포함
                replies = data.get("replies", [])
                for i, reply in enumerate(replies):
                    reply_text = reply.get("text", "")
                    if reply_text:
                        lines.append(f"**트윗 {i+2}:**\n{reply_text}\n")
                return "\n---\n".join(lines)
    except Exception as e:
        print(f"vxtwitter 실패: {e}", file=sys.stderr)
    return None


def fetch_via_jina(url: str) :
    """Jina Reader로 스레드 추출 (fallback 2)."""
    try:
        import requests
        resp = requests.get(f"https://r.jina.ai/{url}", timeout=30,
                            headers={"Accept": "text/markdown"})
        if resp.status_code == 200 and len(resp.text) > 100:
            return resp.text
    except Exception as e:
        print(f"jina 실패: {e}", file=sys.stderr)
    return None


def save_clipping(url: str, content: str, output_dir: Path,
                  method: str = "nitter", validation=None) -> Path:
    now = datetime.now()
    date = now.strftime("%Y%m%d")
    slug = slug_from_url(url)
    filename = f"{date}-{slug}.md"

    if validation is None:
        validation = validate_content(content, source_type="thread", url=url)

    # X.com 스레드는 항상 general 또는 원문 분석에 맡김
    front_matter_data = {
        "source_url": url,
        "source_type": "thread",
        "created": now.date().isoformat(),
        "clipped_at": now.isoformat(),
        "title": f"X.com Thread — {slug}",
        "author": "",
        "word_count": validation.word_count,
        "fetch_method": method,
        # VERIFY 게이트 — 본문이 아닌 것은 clipped 로 두지 않는다.
        "status": "fetch_failed" if validation.failed else "clipped",
        "domain_hint": "general",
        "tags": ["thread", "x.com"],
    }
    if not validation.ok:
        front_matter_data["content_warning"] = validation.code
        front_matter_data["content_warning_reason"] = validation.reason
    front_matter = f"---\n{yaml.dump(front_matter_data, allow_unicode=True, default_flow_style=False).strip()}\n---\n\n원본: [{url}]({url})\n\n---\n\n{content}\n\n---\n*원문: [{url}]({url})*\n"
    path = output_dir / filename
    path.write_text(front_matter)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    output_dir = Path(args.output) if args.output else Path(__file__).parent.parent / "clippings"
    output_dir.mkdir(exist_ok=True)

    url = args.url

    # 추출기를 순서대로 시도하되, **검증을 통과한 첫 결과**만 채택한다.
    # (기존에는 무언가 반환되기만 하면 폴백을 멈춰 에러 페이지가 그대로 저장됐다)
    best = None
    method = "failed"
    content = ""
    validation = None
    for name, fn in (("nitter", fetch_via_nitter),
                     ("vxtwitter", fetch_via_vxtwitter),
                     ("jina", fetch_via_jina)):
        try:
            candidate = fn(url)
        except Exception as e:
            print(f"{name} 예외: {e}", file=sys.stderr)
            continue
        if not candidate:
            continue
        v = validate_content(candidate, source_type="thread", url=url)
        if v.usable:
            content, method, validation = candidate, name, v
            if not v.ok:
                print(f"{name}: {v}", file=sys.stderr)
            break
        print(f"{name} 검증 실패 → 폴백: {v}", file=sys.stderr)
        if best is None or v.word_count > best[2].word_count:
            best = (candidate, name, v)

    if not content:
        if best:
            content, method, validation = best
        else:
            content = f"[스레드 추출 실패 — 수동 처리 필요]\n원본: {url}"
            validation = validate_content(content, source_type="thread", url=url)

    path = save_clipping(url, content, output_dir, method=method, validation=validation)
    print(f"저장: {path}")
    if validation.failed:
        print(f"경고: 본문 검증 실패 ({validation}) → status: fetch_failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
