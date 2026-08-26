"""클리핑 중복 방지 회귀 테스트.

동일 URL·동일 본문이 반복 클립되어 near-duplicate 노트 클러스터를 양산하던 결함을
고정한다. 실측 피해: 동일 Medium URL 3회 클립(+ _quarantine 8건) → 같은 주제 노트
약 40개, `openai-deployment-company` 엔티티 노트 4개 중복.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetchers.utils import (  # noqa: E402
    content_fingerprint,
    find_existing_clipping,
    _normalize_url_for_dedup,
)

BODY = "충분히 긴 본문 문장이 이어진다. " * 12


def _clipping(url: str = "", *, status: str = "clipped", body: str = BODY,
              source_type: str = "url") -> str:
    return (
        f"---\nsource_url: \"{url}\"\nsource_type: {source_type}\n"
        f"status: {status}\ntitle: t\n---\n\n{body}\n"
    )


def test_dedup_finds_clipping_inside_quarantine(tmp_path: Path):
    """평면 glob 만 보면 격리된 클리핑이 dedup 에서 빠져 같은 URL 이 재클립된다."""
    url = "https://example.com/article"
    q = tmp_path / "_quarantine"
    q.mkdir()
    (q / "20260530-original.md").write_text(_clipping(url), encoding="utf-8")

    assert find_existing_clipping(url, tmp_path) is not None


def test_dedup_ignores_failed_clipping_so_refetch_is_allowed(tmp_path: Path):
    """추출 실패분을 dedup 대상으로 인정하면 원문을 영구히 못 받는다."""
    url = "https://example.com/blocked"
    (tmp_path / "20260601-blocked.md").write_text(
        _clipping(url, status="fetch_failed", body="403 Forbidden"), encoding="utf-8"
    )
    assert find_existing_clipping(url, tmp_path) is None


def test_dedup_matches_by_content_when_url_absent(tmp_path: Path):
    """로컬 파일 소스는 URL 이 없어 본문 지문으로만 잡을 수 있다."""
    (tmp_path / "20260825-bundle.md").write_text(
        _clipping("", source_type="text"), encoding="utf-8"
    )
    incoming = _clipping("", source_type="text")
    assert find_existing_clipping("", tmp_path, content=incoming) is not None


def test_content_fingerprint_ignores_whitespace_differences():
    a = content_fingerprint(f"---\ntitle: a\n---\n\n{BODY}")
    b = content_fingerprint(f"---\ntitle: b\n---\n\n{BODY.replace(' ', '  ')}")
    assert a and a == b


def test_content_fingerprint_empty_for_trivial_body():
    """짧은 본문은 지문을 내지 않는다 — 서로 다른 스텁이 뭉치는 것을 막는다."""
    assert content_fingerprint("---\ntitle: t\n---\n\n짧다\n") == ""


def test_content_fingerprint_differs_for_different_bodies():
    assert content_fingerprint(BODY) != content_fingerprint(BODY + " 추가 문장이 붙는다.")


def test_url_normalization_strips_tracking_params():
    a = _normalize_url_for_dedup("https://example.com/a?utm_source=tw&fbclid=1")
    b = _normalize_url_for_dedup("https://example.com/a/")
    assert a == b


def test_x_share_params_are_stripped():
    """같은 트윗이 ?s=52 / ?s=20 / 무파라미터로 각각 다른 URL 취급되면 재클립된다."""
    base = "https://x.com/user/status/123"
    assert (_normalize_url_for_dedup(base + "?s=52")
            == _normalize_url_for_dedup(base + "?s=20")
            == _normalize_url_for_dedup(base))


def test_search_param_preserved_outside_x():
    """WordPress 등에서 `?s=` 는 검색어다 — 전역으로 지우면 안 된다."""
    a = _normalize_url_for_dedup("https://blog.example.com/?s=zettelkasten")
    b = _normalize_url_for_dedup("https://blog.example.com/")
    assert a != b


def test_no_false_match_for_different_urls(tmp_path: Path):
    (tmp_path / "20260601-a.md").write_text(
        _clipping("https://example.com/a"), encoding="utf-8"
    )
    assert find_existing_clipping("https://example.com/b", tmp_path) is None
