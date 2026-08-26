"""소실 링크 해제(normalize stage 4) 회귀 테스트.

핵심 제약 두 가지를 고정한다.
1. 프론트매터는 절대 건드리지 않는다 — `source_clipping` 은 pipeline_state 가
   "처리 완료" 판정에 쓰는 필드다. 여기서 지우면 클리핑이 전부 재생성된다.
2. 살아있는 링크와 개념 stub 링크는 건드리지 않는다 — stub 은 디지털 가든의 의도다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import normalize  # noqa: E402


def _setup(tmp_path: Path, monkeypatch, existing: list[str]) -> None:
    """볼트 stem 집합을 테스트용으로 고정한다."""
    monkeypatch.setattr(normalize, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(normalize, "_VAULT_STEMS", set(existing))


def _write(tmp_path: Path, body: str, fm: str = 'title: t\nstatus: publish\n') -> Path:
    p = tmp_path / "note.md"
    p.write_text(f"---\n{fm}---\n\n{body}", encoding="utf-8")
    return p


def test_dead_dated_link_becomes_plain_text(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, existing=[])
    p = _write(tmp_path, "- [[20260508-gone-clipping]] — 설명이 붙어 있다\n")
    n = normalize.normalize_file_dead_links(p)
    out = p.read_text(encoding="utf-8")
    assert n >= 1
    assert "[[" not in out
    assert "`20260508-gone-clipping`" in out        # 식별자는 코드 표기로 보존
    assert "설명이 붙어 있다" in out


def test_alias_is_preserved(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, existing=[])
    p = _write(tmp_path, "출처: [[20260510-gone|원문 클리핑]] · https://example.com/a\n")
    normalize.normalize_file_dead_links(p)
    out = p.read_text(encoding="utf-8")
    assert "원문 클리핑" in out
    assert "[[" not in out
    assert "https://example.com/a" in out           # 인접 URL 로 출처는 추적 가능


def test_live_link_untouched(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, existing=["20260508-alive"])
    p = _write(tmp_path, "- [[20260508-alive]] — 살아있는 링크\n")
    assert normalize.normalize_file_dead_links(p) == 0
    assert "[[20260508-alive]]" in p.read_text(encoding="utf-8")


def test_concept_stub_untouched(tmp_path, monkeypatch):
    """개념 stub 은 날짜 접두사가 없다 — 디지털 가든의 의도된 링크다."""
    _setup(tmp_path, monkeypatch, existing=[])
    body = "- [[zettelkasten]]\n- [[메타인지]]\n- [[Ralph Loop]]\n- [[KV cache]]\n"
    p = _write(tmp_path, body)
    assert normalize.normalize_file_dead_links(p) == 0
    assert p.read_text(encoding="utf-8").endswith(body)


def test_frontmatter_source_clipping_untouched(tmp_path, monkeypatch):
    """pipeline_state 가 읽는 필드다. 지우면 클리핑이 재생성된다."""
    _setup(tmp_path, monkeypatch, existing=[])
    fm = 'title: t\nsource_clipping: "[[20260508-gone-clipping]]"\nstatus: publish\n'
    p = _write(tmp_path, "본문에는 링크가 없다.\n", fm=fm)
    assert normalize.normalize_file_dead_links(p) == 0
    assert 'source_clipping: "[[20260508-gone-clipping]]"' in p.read_text(encoding="utf-8")


def test_frontmatter_relations_target_untouched(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, existing=[])
    fm = ('title: t\nrelations:\n  - type: SUPPORTS\n'
          '    target: "[[20260611-gone-note]]"\n    evidence: "근거"\n')
    p = _write(tmp_path, "본문.\n", fm=fm)
    assert normalize.normalize_file_dead_links(p) == 0
    assert 'target: "[[20260611-gone-note]]"' in p.read_text(encoding="utf-8")


def test_frontmatter_untouched_but_body_cleaned(tmp_path, monkeypatch):
    """같은 타깃이 프론트매터와 본문에 모두 있을 때 본문만 정리한다."""
    _setup(tmp_path, monkeypatch, existing=[])
    fm = 'title: t\nsource_clipping: "[[20260508-gone]]"\n'
    p = _write(tmp_path, "출처: [[20260508-gone|클리핑]]\n", fm=fm)
    assert normalize.normalize_file_dead_links(p) >= 1
    out = p.read_text(encoding="utf-8")
    assert 'source_clipping: "[[20260508-gone]]"' in out
    assert "출처: 클리핑" in out


def test_idempotent(tmp_path, monkeypatch):
    """두 번 돌려 두 번째에는 변화가 없어야 한다 — normalize.py 의 계약."""
    _setup(tmp_path, monkeypatch, existing=[])
    p = _write(tmp_path, "- [[20260508-gone]] — 설명\n출처: [[20260510-gone2|원문]]\n")
    first = normalize.normalize_file_dead_links(p)
    after_first = p.read_text(encoding="utf-8")
    assert first >= 1
    assert normalize.normalize_file_dead_links(p) == 0
    assert p.read_text(encoding="utf-8") == after_first


def test_bare_slug_becomes_code_span(tmp_path, monkeypatch):
    """맨 슬러그는 코드 표기로 남긴다 — 산문으로 오해되지 않고 grep 으로 찾을 수 있다."""
    _setup(tmp_path, monkeypatch, existing=[])
    p = _write(tmp_path, "- [[20260508-gone]] — 설명\n")
    normalize.normalize_file_dead_links(p)
    assert "`20260508-gone`" in p.read_text(encoding="utf-8")


def test_heading_anchor_link_handled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, existing=[])
    p = _write(tmp_path, "[[20260508-gone#핵심]] 참고\n")
    normalize.normalize_file_dead_links(p)
    assert "[[" not in p.read_text(encoding="utf-8")
