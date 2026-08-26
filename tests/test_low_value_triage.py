"""저가치(광고·유인) 신호 + 트리아지 검증 계약 테스트.

VERIFY 와 CLASSIFY 는 성질이 다르다.
- VERIFY(추출 실패)는 기계가 확정할 수 있다 → 코드가 차단한다.
- CLASSIFY(가치 판단)는 기계가 확정할 수 없다 → 신호만 내고 판정은 LLM 이 한다.

그래서 CLASSIFY 는 "건너뛰어도 아무 흔적이 없다"는 구조적 약점이 있었다. 규칙을 넣은
뒤에도 볼트 전체에 `classification` 필드를 가진 노트가 0개였다. 이 테스트는
① 기계 신호가 실제 스팸을 잡고 정상 글은 통과시키는지
② 트리아지 건너뜀이 관측 가능한지
를 고정한다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from content_validation import (  # noqa: E402
    LOW_VALUE, OK, SUSPECT,
    low_value_signals, validate_content,
)

# 실제 사고 입력: 42단어 리딩방 유인 스레드가 wiki/concepts 개념 노트로 승격됐다.
REAL_SPAM = """52세 주식 투자 19년 자산 72억

나는 이 5가지만 지킨다:

1️⃣ 주가가 12%를 넘겼는데도 상한가를 못 잠그면 → 즉시 정리

2️⃣ 시초가가 -4% 이하로 시작하고, 장중에 양봉 전환이 안 되면 → 바로 손절

3️⃣ 저점 구간인데 거래량이 없다면 → 서두르지 말고 관망
"""


# ------------------------------------------------------------------ 신호 발화

def test_real_spam_is_flagged_low_value():
    r = validate_content(REAL_SPAM, source_type="thread")
    assert r.verdict == SUSPECT
    assert r.code == LOW_VALUE
    assert {"brag", "secret"} <= set(low_value_signals(REAL_SPAM))


def test_cta_lure_is_flagged():
    body = "월 500만원 수익 인증합니다. 비법 무료 공개. 오픈채팅 링크 클릭하세요."
    r = validate_content(body, source_type="thread")
    assert r.code == LOW_VALUE, r


def test_english_lure_is_flagged():
    body = "I made 10배 수익 with this. The secret 비법 nobody tells you. DM me to get started free."
    assert len(set(low_value_signals(body))) >= 2


# --------------------------------------------------- 오탐 방지(정상 글 통과)

def test_legit_analysis_mentioning_returns_passes():
    """정상 투자 분석은 수익률을 언급한다 — 한 범주만 맞으면 통과해야 한다."""
    body = ("반도체 섹터의 지난 3년 수익률 42%를 분기별로 분해하면 실적 성장분과 "
            "멀티플 확장분이 6:4로 나뉜다. ") * 6
    r = validate_content(body, source_type="thread")
    assert r.verdict == OK, r
    assert len(set(low_value_signals(body))) < 2


def test_legit_list_article_passes():
    """'N가지만' 프레이밍은 정상 리스트글에도 쓰인다."""
    body = ("실무에서 실제로 쓰는 5가지만 정리했다. 각 항목은 재현 절차와 "
            "실패 조건을 함께 적었다. ") * 6
    r = validate_content(body, source_type="thread")
    assert r.verdict == OK, r


def test_long_body_not_flagged():
    """긴 본문은 실제 내용이 있을 가능성이 높아 이 신호를 적용하지 않는다."""
    body = REAL_SPAM + ("이어서 각 규칙의 근거를 백테스트 결과와 함께 상세히 설명한다. " * 60)
    r = validate_content(body, source_type="url")
    assert r.code != LOW_VALUE, r


def test_single_category_is_not_enough():
    """한 범주만 맞는 것은 정상 글에서도 흔하다 — 2개 이상일 때만 신호."""
    body = "자산 30억을 모은 과정을 회계 기준으로 분해해 기록한다. " * 5
    assert len(set(low_value_signals(body))) == 1
    assert validate_content(body, source_type="thread").verdict == OK


# ----------------------------------------------- 트리아지 건너뜀이 보이는가

def test_triage_audit_detects_missing_classification(tmp_path, monkeypatch):
    """classification 없는 노트를 잡아내야 한다 — 건너뜀을 관측 가능하게 하는 지점."""
    import pipeline
    monkeypatch.setattr(pipeline, "PROJECT_DIR", tmp_path)
    (tmp_path / "a.md").write_text('---\ntitle: t\nstatus: publish\n---\n\n본문\n', encoding="utf-8")
    (tmp_path / "b.md").write_text(
        '---\ntitle: t\nclassification: useful\nclassification_reason: "방법론 제시"\n---\n\n본문\n',
        encoding="utf-8")
    res = pipeline._audit_triage("stem", ["a.md", "b.md"])
    assert res["classified"] == 1
    assert res["missing"] == ["a.md"]
    assert res["ad"] == []


def test_triage_audit_flags_ad_notes(tmp_path, monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, "PROJECT_DIR", tmp_path)
    (tmp_path / "c.md").write_text(
        '---\ntitle: t\nclassification: ad\nclassification_reason: "링크 덤프"\n---\n\n본문\n',
        encoding="utf-8")
    res = pipeline._audit_triage("stem", ["c.md"])
    assert res["ad"] == ["c.md"]
    assert res["missing"] == []
