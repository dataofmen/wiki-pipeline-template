"""원문 유효성 검증 — VERIFY 게이트의 단일 진실 원천(SSOT).

fetch_url.py / fetch_thread.py / fetcher_bridge.py / pipeline.py /
audit_clippings.py 가 모두 이 모듈을 통해 판정한다.

배경
----
Jina Reader 는 원본이 500 이든 로그인 벽이든 CAPTCHA 든 **자기 HTTP 응답은 200**
으로 주고 에러 페이지 본문을 그대로 실어 보낸다. 따라서 기존 판정식

    if resp.status_code == 200 and len(resp.text) > 200:   # ← 성공 판정

은 성립하지 않는다. 이 때문에 폴백 체인(jina → readability → playwright)이 한 번도
발동하지 않고 에러 페이지가 `status: clipped` 로 저장됐고, note-maker 가 URL 슬러그만
보고 노트 5개를 환각 생성한 사고가 있었다.
(근거: clippings/20260611-medium.md — Medium 500 페이지 41단어 → wiki 개념 2 + 엔티티 1
 + garden 2 생성. 2026-06-11)

판정 등급
--------
- OK       : 정상 본문. 그대로 진행.
- SUSPECT  : 본문일 수도 있으나 신뢰도 낮음(짧음/추출 신호 불량).
             저장은 하되 `content_warning` 을 남기고 노트 생성 시 보수적으로 다룬다.
- FAILED   : 본문이 아님이 명확(봇 차단/로그인 벽/HTTP 에러/추출 실패).
             `status: fetch_failed` 로 저장하고 노트 생성 대상에서 제외한다.

설계 원칙
--------
마커 하나로 단정하지 않는다. Jina 의 CAPTCHA 경고는 정상 추출된 페이지에도 붙기
때문에, "경고 + 본문이 짧다" 처럼 **신호를 조합**해서만 FAILED 로 승격한다.
정상적인 짧은 X 스레드(하위 10% = 91단어)를 죽이지 않는 것이 이 모듈의 제약이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 등급 / 코드

OK = "ok"
SUSPECT = "suspect"
FAILED = "failed"

# 실패 코드
EXTRACTION_FAILED = "extraction_failed"   # 파이프라인이 남긴 명시적 실패 마커
BOT_BLOCK = "bot_block"                   # Cloudflare / CloudFront / 403
HTTP_ERROR = "http_error"                 # 원본 서버 4xx·5xx 에러 페이지
LOGIN_WALL = "login_wall"                 # 로그인·가입 요구 화면
JS_REQUIRED = "js_required"               # JS 미실행 안내 페이지
EMPTY = "empty"                           # 본문 사실상 없음
TOO_SHORT = "too_short"                   # 본문이 임계치 미달
LOW_CONFIDENCE = "low_confidence"         # 추출기 경고 + 짧은 본문
LINK_ONLY = "link_only"                   # 링크 한 줄만 있고 본문이 없음
NO_DATA = "no_data"                       # 상위 작업(cron 등)이 빈 결과를 남김

# ------------------------------------------------------- 소스 타입별 최소 단어수
#
# clippings/ 실측 분포(n=210): min=1, p10=91, median=830, p90=5375.
# 스레드/텍스트는 정상적으로도 짧아 임계치를 낮게 잡는다.
MIN_WORDS: dict[str, int] = {
    "url": 80,
    "pdf": 80,
    "thread": 25,
    "text": 25,
    "text-file": 25,
    "image": 10,
}
DEFAULT_MIN_WORDS = 60

# 이 길이 아래에서만 "문맥 마커"를 실패 신호로 인정한다(오탐 방지).
CONTEXT_MARKER_MAX_WORDS = 400
# 문맥 마커를 탐색할 본문 앞부분 길이(문자).
CONTEXT_HEAD_CHARS = 900

# --------------------------------------------------------------------- 마커

# 어디에 나오든 본문이 아님이 확실한 마커.
HARD_MARKERS: list[tuple[str, str, str]] = [
    # (코드, 마커, 사람이 읽는 설명)
    (EXTRACTION_FAILED, "[추출 실패", "fetch_url 추출 실패 마커"),
    (EXTRACTION_FAILED, "[원문 추출 실패", "원문 추출 실패 마커"),
    (EXTRACTION_FAILED, "[스레드 추출 실패", "스레드 추출 실패 마커"),
    (EXTRACTION_FAILED, "[PDF 추출 실패", "PDF 추출 실패 마커"),
    (EXTRACTION_FAILED, "[이미지 추출 실패", "이미지 추출 실패 마커"),
    (EXTRACTION_FAILED, "[fetch failed]", "fetch 실패 마커"),
    (BOT_BLOCK, "The request could not be satisfied", "CloudFront 차단"),
    (BOT_BLOCK, "Request blocked", "요청 차단"),
    (BOT_BLOCK, "403 Forbidden", "HTTP 403"),
    (BOT_BLOCK, "403 ERROR", "HTTP 403"),
    (BOT_BLOCK, "cloudfront (CloudFront)", "CloudFront 차단"),
    (BOT_BLOCK, "Attention Required! | Cloudflare", "Cloudflare 차단"),
    (BOT_BLOCK, "Checking if the site connection is secure", "Cloudflare 인터스티셜"),
    (BOT_BLOCK, "Verifying you are human", "봇 차단 인터스티셜"),
    (BOT_BLOCK, "Access Denied", "접근 거부"),
    (HTTP_ERROR, "Apologies, but something went wrong on our end", "Medium 500 에러 페이지"),
    (HTTP_ERROR, "This page isn’t working", "서버 에러 페이지"),
    (HTTP_ERROR, "504 Gateway Time-out", "HTTP 504"),
    (HTTP_ERROR, "502 Bad Gateway", "HTTP 502"),
    (LOGIN_WALL, "권혁민님, 무슨 생각을 하고 계신가요?", "Facebook 로그인 벽"),
    # 상위 작업이 빈 결과를 남긴 경우 (overnight cron 브리핑 등)
    (NO_DATA, "No briefing data available", "cron 브리핑 데이터 없음"),
    (NO_DATA, "이전 cron 작업이 실패", "cron 작업 실패"),
    (NO_DATA, "제공된 내용이 없습니다", "본문 미제공"),
    (NO_DATA, "현재 데이터가 없으며", "데이터 없음"),
]

# 본문이 짧을 때에만 실패로 인정하는 문맥 마커(앞부분에서 탐색).
CONTEXT_MARKERS: list[tuple[str, str, str]] = [
    (LOGIN_WALL, "Sign in to continue", "로그인 요구"),
    (LOGIN_WALL, "Please sign in to continue", "로그인 요구"),
    (LOGIN_WALL, "Welcome back! Please sign in", "로그인 화면"),
    (LOGIN_WALL, "Sign in to your account", "로그인 화면"),
    (LOGIN_WALL, "Create your account", "가입 화면"),
    (LOGIN_WALL, "Continue with Google", "소셜 로그인 화면"),
    (LOGIN_WALL, "Log in to continue", "로그인 요구"),
    (LOGIN_WALL, "로그인이 필요합니다", "로그인 요구"),
    (LOGIN_WALL, "계속하려면 로그인", "로그인 요구"),
    (LOGIN_WALL, "You must log in to continue", "로그인 요구"),
    (LOGIN_WALL, "Subscribe to continue reading", "페이월"),
    (LOGIN_WALL, "This content is for members", "페이월"),
    (JS_REQUIRED, "Enable JavaScript and cookies to continue", "JS 요구"),
    (JS_REQUIRED, "Please enable JavaScript to view", "JS 요구"),
    (JS_REQUIRED, "You need to enable JavaScript to run this app", "JS 요구"),
    (JS_REQUIRED, "Just a moment...", "Cloudflare 대기 페이지"),
    # Akamai Bot Manager 인터스티셜. 본문 전체가 이 푸터뿐이면 차단 페이지다.
    # (실측: 쿠팡 단축링크 link.coupang.com 이 이 페이지를 반환)
    (BOT_BLOCK, "Powered and protected by", "Akamai 봇 차단 인터스티셜"),
]

# 추출기(Jina)가 붙이는 경고 — 단독으로는 실패가 아니다. 짧은 본문과 결합할 때만 신호.
EXTRACTOR_WARNINGS: list[str] = [
    "Warning: This page maybe requiring CAPTCHA",
    "Warning: Target URL returned error",
    "Warning: This page contains shadow DOM",
    "please make sure you are authorized to access this page",
]

# 본문 앞부분에 알몸으로 놓인 HTTP 에러 코드 한 줄 (예: Medium 500 페이지의 "500").
BARE_HTTP_STATUS_RE = re.compile(r"^\s*(?:#+\s*)?([45]\d{2})\s*$", re.MULTILINE)

FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
SOURCE_FOOTER_RE = re.compile(r"\n*---\s*\n\*원문:.*?$", re.DOTALL)

# 중국어·일본어는 어절을 공백으로 나누지 않아 `split()` 이 단어수를 심하게 과소집계한다.
# (실측: 2,000자 분량의 중국어 스레드가 word_count 20 으로 기록됨 → 오탐 발생)
# 한글은 공백을 쓰므로 제외한다.
CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
CJK_CHARS_PER_WORD = 2

URL_RE = re.compile(r"https?://\S+")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# `(없음)`, `(none)`, `(내용 없음)` 처럼 채워지지 않은 템플릿 자리표시자 한 줄.
PLACEHOLDER_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\(.{0,60}\)\s*$")
HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s")


# --------------------------------------------------------------------- 결과

@dataclass
class ValidationResult:
    verdict: str                      # OK | SUSPECT | FAILED
    code: str = OK                    # 대표 실패 코드
    reason: str = ""                  # 사람이 읽는 사유
    word_count: int = 0
    signals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == OK

    @property
    def failed(self) -> bool:
        return self.verdict == FAILED

    @property
    def usable(self) -> bool:
        """노트 생성을 허용할지 여부. SUSPECT 는 경고를 달고 통과시킨다."""
        return self.verdict in (OK, SUSPECT)

    def __str__(self) -> str:
        if self.verdict == OK:
            return f"ok ({self.word_count}단어)"
        return f"{self.verdict}:{self.code} — {self.reason} ({self.word_count}단어)"


# ----------------------------------------------------------------- 본문 추출

def strip_frontmatter(text: str) -> str:
    """YAML 프론트매터를 제거한다. 본문 안의 `---` 에 영향받지 않는다."""
    return FRONTMATTER_RE.sub("", text, count=1)


def extract_body(text: str) -> str:
    """프론트매터·출처 푸터·`원본:` 링크 줄을 뺀 실제 본문."""
    body = strip_frontmatter(text)
    body = SOURCE_FOOTER_RE.sub("", body)
    lines = [
        ln for ln in body.splitlines()
        if not ln.startswith("원본:") and not ln.startswith("*원문")
    ]
    return "\n".join(lines).strip()


def effective_word_count(text: str) -> int:
    """CJK 를 감안한 단어수. 한자·가나는 2자를 1단어로 환산한다."""
    cjk = len(CJK_RE.findall(text))
    non_cjk = CJK_RE.sub(" ", text)
    return len(non_cjk.split()) + round(cjk / CJK_CHARS_PER_WORD)


def substantive_text(body: str) -> str:
    """URL·마크다운 링크·제목 줄·빈 자리표시자를 뺀 실질 본문.

    "링크만 있는 스텁" 과 "채워지지 않은 템플릿" 을 구분하기 위한 것이다.
    """
    kept = []
    for ln in body.splitlines():
        if HEADING_LINE_RE.match(ln):
            continue
        if PLACEHOLDER_LINE_RE.match(ln):
            continue
        stripped = MD_LINK_RE.sub(r"\1", ln)
        stripped = URL_RE.sub("", stripped)
        stripped = stripped.strip(" \t-*·|")
        if stripped:
            kept.append(stripped)
    return "\n".join(kept).strip()


def _strip_extractor_warnings(body: str) -> str:
    """Jina 경고 줄과 `Title:` 헤더를 뺀 순수 본문 (단어수 계산용)."""
    kept = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("Warning:"):
            continue
        if re.match(r"^Title:\s*", s):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


# ----------------------------------------------------------------- 판정 본체

def validate_content(
    content: str,
    *,
    source_type: str = "url",
    url: str = "",
    title: str = "",
    min_words: int | None = None,
) -> ValidationResult:
    """원문 텍스트를 검증한다. `content` 는 프론트매터 유무 무관.

    Args:
        content: 추출된 텍스트(또는 클리핑 파일 전체).
        source_type: url | thread | pdf | text | image — 최소 단어수를 좌우한다.
        url: 판정 사유 메시지용(선택).
        title: 제목도 함께 본다 — "Sign in to My Admin" 같은 제목이 강한 신호다.
        min_words: 소스 타입 기본값을 덮어쓴다.
    """
    body = extract_body(content or "")
    clean = _strip_extractor_warnings(body)
    words = effective_word_count(clean)
    substantive = substantive_text(clean)
    threshold = min_words if min_words is not None else MIN_WORDS.get(source_type, DEFAULT_MIN_WORDS)

    signals: list[str] = []
    haystack = f"{title}\n{body}"
    head = f"{title}\n{clean[:CONTEXT_HEAD_CHARS]}"

    # 1) 확정 마커 — 길이 무관하게 실패.
    for code, marker, label in HARD_MARKERS:
        if marker in haystack:
            return ValidationResult(FAILED, code, label, words, [f"marker:{marker}"])

    # 2) 본문이 거의 없음. 제목·URL·빈 자리표시자를 빼고 판단한다.
    if words == 0 or len(substantive) < 40:
        return ValidationResult(
            FAILED, EMPTY,
            f"실질 본문이 비어 있음(제목·링크·자리표시자 제외 {len(substantive)}자)",
            words, ["empty"],
        )

    # 3) 링크 한두 줄이 본문 전부인 스텁 — 정작 읽을 대상은 그 링크 안에 있다.
    #    (실측: Andrew Ng 트윗이 제목 + x.com/i/article 링크만 남아 clipped 처리됨)
    if URL_RE.search(clean) and words < MIN_WORDS.get("thread", 25):
        if len([ln for ln in substantive.splitlines() if ln.strip()]) <= 2:
            return ValidationResult(
                FAILED, LINK_ONLY,
                f"링크 외 본문이 {words}단어뿐 — 대상 링크를 별도 추출해야 함",
                words, ["link_only"],
            )

    # 3) 알몸 HTTP 에러 코드 한 줄 + 짧은 본문 → 에러 페이지.
    m = BARE_HTTP_STATUS_RE.search(clean[:CONTEXT_HEAD_CHARS])
    if m and words < CONTEXT_MARKER_MAX_WORDS:
        return ValidationResult(
            FAILED, HTTP_ERROR, f"본문 앞부분에 HTTP {m.group(1)} 에러 코드", words,
            [f"bare_status:{m.group(1)}"],
        )

    # 4) 문맥 마커(로그인 벽·JS 요구) — 짧은 본문에서만 실패로 인정.
    if words < CONTEXT_MARKER_MAX_WORDS:
        for code, marker, label in CONTEXT_MARKERS:
            if marker.lower() in head.lower():
                return ValidationResult(
                    FAILED, code, f"{label} (마커: {marker!r})", words, [f"context:{marker}"],
                )

    # 5) 추출기 경고 수집 — 단독으로는 실패가 아니다.
    warnings = [w for w in EXTRACTOR_WARNINGS if w in body]
    if warnings:
        signals.extend(f"warning:{w}" for w in warnings)

    # 6) 경고 + 임계치 미달 → 실패로 승격.
    if warnings and words < threshold:
        return ValidationResult(
            FAILED, LOW_CONFIDENCE,
            f"추출기 경고 {len(warnings)}건 + 본문 {words}단어(임계 {threshold})",
            words, signals,
        )

    # 7) 임계치 미달 — 정상 단문일 수 있어 SUSPECT 로만 표시.
    if words < threshold:
        signals.append(f"short:{words}<{threshold}")
        return ValidationResult(
            SUSPECT, TOO_SHORT,
            f"본문 {words}단어 — {source_type} 임계 {threshold} 미달, 검토 필요",
            words, signals,
        )

    # 8) 경고만 있고 길이는 충분 → SUSPECT.
    if warnings:
        return ValidationResult(
            SUSPECT, LOW_CONFIDENCE,
            f"추출기 경고 {len(warnings)}건 — 본문 확인 권장", words, signals,
        )

    return ValidationResult(OK, OK, "", words, signals)


def validate_clipping_file(path: str | Path, *, source_type: str = "") -> ValidationResult:
    """저장된 클리핑 파일을 검증한다. 프론트매터의 source_type/title 을 활용한다."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return ValidationResult(FAILED, EMPTY, f"파일 읽기 실패: {e}", 0, ["unreadable"])

    fm = FRONTMATTER_RE.match(text)
    st = source_type
    title = ""
    if fm:
        head = fm.group(0)
        if not st:
            m = re.search(r"^source_type:\s*[\"']?([\w-]+)", head, re.MULTILINE)
            st = m.group(1) if m else "url"
        m = re.search(r"^title:\s*[\"']?(.*?)[\"']?\s*$", head, re.MULTILINE)
        title = m.group(1) if m else ""
    return validate_content(text, source_type=st or "url", title=title)


# --------------------------------------------------------------------- CLI

def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="클리핑/원문 유효성 검증")
    ap.add_argument("paths", nargs="+", help="검증할 클리핑 파일 경로")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--failed-only", action="store_true", help="FAILED 만 출력")
    args = ap.parse_args()

    rows = []
    for path in args.paths:
        r = validate_clipping_file(path)
        if args.failed_only and not r.failed:
            continue
        rows.append({
            "path": path, "verdict": r.verdict, "code": r.code,
            "reason": r.reason, "word_count": r.word_count, "signals": r.signals,
        })

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row['verdict']:<8} {row['code']:<18} {row['word_count']:>6}단어  "
                  f"{Path(row['path']).name}  {row['reason']}")
    return 1 if any(r["verdict"] == FAILED for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
