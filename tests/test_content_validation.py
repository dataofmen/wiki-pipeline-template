"""VERIFY 게이트 회귀 테스트.

실제 사고에서 나온 입력을 그대로 고정한다. 이 테스트가 깨지면 에러 페이지가
다시 `clipped` 로 유입될 수 있다는 뜻이다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from content_validation import (  # noqa: E402
    extract_body,
    FAILED, OK, SUSPECT,
    HTTP_ERROR, LOGIN_WALL, LINK_ONLY, NO_DATA, EMPTY, TOO_SHORT,
    effective_word_count, substantive_text, validate_content,
)


# --------------------------------------------------------------- 실제 사고 입력

MEDIUM_500 = """Title: Medium

Warning: This page maybe requiring CAPTCHA, please make sure you are authorized to access this page.

500

## Apologies, but something went wrong on our end.

Refresh the page, check [Medium's site status](https://status.medium.com/), or [find something interesting to read](https://medium.com/browse/top).
"""

CLERK_LOGIN = """Title: Sign in to My Admin

Warning: This page contains shadow DOM that are currently hidden.
Warning: This page maybe requiring CAPTCHA, please make sure you are authorized to access this page.

[Artifact Hub](https://artifact-hub-virid.vercel.app/)

Sign in

# Sign in to My Admin

Welcome back! Please sign in to continue

Continue with Google

or

Email address

Password

Continue

Secured by
"""

LINK_ONLY_TWEET = (
    "New: A map of the most important skills in AI Engineering. "
    "https://x.com/i/article/2088296780983107584"
)

CRON_NO_DATA = """# 2026-08-24 Overnight AI Research

## Summary
No briefing data available.

## Raw Briefing

## Sources

## Keywords
"""

CRON_PLACEHOLDERS = """# 2026-08-25 Overnight AI Research

## Summary
(07:00 브리핑의 핵심 요약 – 현재 데이터가 없으며, 이전 cron 작업이 실패했습니다.)

## Raw Briefing
(07:00 브리핑 원문 – 제공된 내용이 없습니다.)

## Sources
- (없음)

## Keywords
- (없음)
"""

SUBSTACK_URL_ONLY = (
    "https://saeidehbakhshi.substack.com/p/the-five-user-rule-was-never-a-rule"
    "?utm\\_campaign=post&utm\\_medium=web&triedRedirect=true"
)

# CJK 는 공백으로 어절을 나누지 않는다 — split() 이면 20단어로 과소집계된다.
CHINESE_THREAD = """科学界宝藏免费素材库来了！

NIH BioArt Source 由美国国家过敏与传染病研究所

（NIAID，隶属NIH）官方出品，完全免费的高质量生物医学插图库！

亮点：

超过 2000+ 张专业插画师绘制的矢量图、图标、笔刷

界面干净、无广告、下载方便

覆盖病毒、细胞、解剖、免疫、蛋白质、实验设备等生物医学几乎所有常见主题

Public Domain（公有领域），可自由用于论文、演示、教学、商业用途

特别适合学生、科研狗、科普作者、科普视频创作者使用。

浏览分类时还能顺便学到不少生物医学专业词汇，一举两得。

官网地址（直接冲）：

https://bioart.niaid.nih.gov

强烈建议收藏！

以后做PPT、写论文、做科普内容时能省下大量找图时间。
"""

KOREAN_SHORT_THREAD = """52세 주식 투자 19년 자산 72억

나는 이 5가지만 지킨다:

1️⃣ 주가가 12%를 넘겼는데도 상한가를 못 잠그면 → 즉시 정리

2️⃣ 시초가가 -4% 이하로 시작하고, 장중에 양봉 전환이 안 되면 → 바로 손절

3️⃣ 저점 구간인데 거래량이 없다면 → 서두르지 말고 관망
"""


# ------------------------------------------------------------------- 실패 판정

def test_medium_500_page_is_failed():
    """Jina 가 200 으로 실어 보낸 Medium 500 페이지. 노트 5개 환각의 원인."""
    r = validate_content(MEDIUM_500, source_type="url", title="Medium")
    assert r.verdict == FAILED
    assert r.code == HTTP_ERROR


def test_clerk_login_wall_is_failed():
    r = validate_content(CLERK_LOGIN, source_type="url", title="Sign in to My Admin")
    assert r.verdict == FAILED
    assert r.code == LOGIN_WALL


def test_link_only_tweet_is_failed():
    """본문이 제목 한 줄 + 링크뿐이면 정작 읽을 대상은 링크 안에 있다."""
    r = validate_content(LINK_ONLY_TWEET, source_type="thread")
    assert r.verdict == FAILED
    assert r.code == LINK_ONLY


def test_cron_no_data_is_failed():
    r = validate_content(CRON_NO_DATA, source_type="url")
    assert r.verdict == FAILED
    assert r.code == NO_DATA


def test_cron_placeholder_body_is_failed():
    r = validate_content(CRON_PLACEHOLDERS, source_type="url")
    assert r.verdict == FAILED
    assert r.code == NO_DATA


def test_url_only_body_is_failed():
    """본문이 리다이렉트 URL 하나뿐 — 실질 본문 0자."""
    r = validate_content(SUBSTACK_URL_ONLY, source_type="url")
    assert r.verdict == FAILED
    assert r.code == EMPTY


def test_explicit_failure_marker_is_failed():
    r = validate_content("[추출 실패 — 수동 처리 필요]\n원본: https://x.test",
                         source_type="url")
    assert r.verdict == FAILED


def test_empty_content_is_failed():
    assert validate_content("", source_type="url").verdict == FAILED


# ------------------------------------------------------- 오탐 방지(정상 통과)

def test_chinese_thread_passes_despite_no_spaces():
    """CJK 를 문자 기준으로 환산하지 않으면 정상 스레드가 too_short 로 죽는다."""
    assert len(CHINESE_THREAD.split()) < 30          # split() 기준으로는 짧아 보인다
    r = validate_content(CHINESE_THREAD, source_type="thread")
    assert r.verdict == OK, r
    assert r.word_count > 100


def test_short_korean_thread_passes():
    """정상적으로 짧은 한글 스레드는 통과해야 한다(스레드 임계 25단어)."""
    r = validate_content(KOREAN_SHORT_THREAD, source_type="thread")
    assert r.verdict == OK, r


def test_long_article_mentioning_sign_in_passes():
    """긴 본문 안의 'Sign in' 언급은 로그인 벽이 아니다 — 문맥 마커 오탐 방지."""
    body = ("이 글은 인증 흐름을 다룬다. Sign in to continue 라는 문구가 "
            "왜 전환율을 떨어뜨리는지 설명한다. ") * 40
    r = validate_content(body, source_type="url")
    assert r.verdict == OK, r


def test_captcha_warning_alone_does_not_fail_long_body():
    """Jina 는 정상 추출한 페이지에도 CAPTCHA 경고를 붙인다 — 단독으로는 실패가 아니다."""
    body = ("Warning: This page maybe requiring CAPTCHA\n\n"
            + "실제 본문 문장이 충분히 길게 이어진다. " * 40)
    r = validate_content(body, source_type="url")
    assert r.verdict == SUSPECT
    assert r.usable


# ------------------------------------------------------------------- 경계 판정

def test_short_url_body_is_suspect_not_failed():
    """임계 미달은 SUSPECT — 저장은 하되 경고를 달고 보수적으로 다룬다."""
    r = validate_content("짧지만 의미 있는 본문이다. " * 4, source_type="url")
    assert r.verdict == SUSPECT
    assert r.code == TOO_SHORT
    assert r.usable and not r.failed


def test_suspect_is_usable_failed_is_not():
    assert validate_content(MEDIUM_500, source_type="url").usable is False


# ------------------------------------------------------------------- 헬퍼 단위

def test_effective_word_count_counts_cjk_by_char():
    assert effective_word_count("hello world") == 2
    assert effective_word_count("免费素材库") == 2          # 5자 / 2 → round(2.5) = 2
    assert effective_word_count("mixed 中文内容 here") == 4  # 2 + round(4/2)
    # 공백 없는 CJK 장문이 split() 기준보다 훨씬 크게 집계돼야 한다
    long_cjk = "免费素材库" * 40
    assert len(long_cjk.split()) == 1
    assert effective_word_count(long_cjk) == 100


def test_substantive_text_strips_urls_headings_placeholders():
    raw = "# 제목\n\nhttps://example.com/a\n- (없음)\n실제 문장이다.\n"
    assert substantive_text(raw) == "실제 문장이다."


def test_frontmatter_is_not_confused_by_body_dashes():
    """본문 안의 `---` 가 프론트매터 파싱을 깨뜨리지 않아야 한다."""
    doc = ("---\ntitle: t\nstatus: clipped\n---\n\n"
           + "본문 문장이 충분히 길다. " * 30 + "\n\n---\n\n뒤쪽 본문도 남는다. " * 5)
    r = validate_content(doc, source_type="url")
    assert r.verdict == OK
    # 프론트매터를 벗기는 책임은 extract_body 에 있다 (substantive_text 는 본문 전용)
    body = extract_body(doc)
    assert "title: t" not in body
    assert "status: clipped" not in body
    assert "뒤쪽 본문도 남는다." in body
