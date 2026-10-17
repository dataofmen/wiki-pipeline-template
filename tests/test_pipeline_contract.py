"""CanonicalUrl and Placement fail the historical rules and pass the types.

The golden assertions are the contract. Pointing them at the pre-type
normalizers fails. ``CanonicalUrl`` and ``apply_publish_fix`` satisfy them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pipeline_contract import (  # noqa: E402
    CanonicalUrl,
    ContractError,
    Placement,
    Publishable,
    Space,
    apply_publish_fix,
    placement_from_parts,
    publishable,
)


def _note(target: str = "machine", note_type: str = "concept") -> str:
    return f"---\ntarget: {target}\ntype: {note_type}\ntitle: t\n---\n\nbody\n"


def _legacy_pipeline_normalize(url: str) -> str:
    """``pipeline._normalize_url`` as of bafc09a: unquote, then parse_qsl unquotes again.

    Tracking denylist only. No host alias. X ``?s=`` stays. WordPress ``?s=`` stays.
    """
    url = url.strip().strip('"').strip("'")
    url = unquote(url)
    parsed = urlparse(url)
    tracking = {
        "si",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
    }
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in tracking]
    return parsed._replace(query=urlencode(query), fragment="").geturl().rstrip("/")


def _legacy_utils_normalize(url: str) -> str:
    """``fetchers.utils._normalize_url_for_dedup`` as of 2734927.

    No percent-decode. ``"x.com" in host`` also matches ``box.com``.
    Hosts are not aliased, so twitter.com and x.com stay different strings.
    """
    url = url.strip().strip('"').strip("'")
    parsed = urlparse(url)
    tracking = {
        "si",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
    }
    host = (parsed.netloc or "").lower()
    if "x.com" in host or "twitter.com" in host:
        tracking = tracking | {"s", "t"}
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in tracking]
    return parsed._replace(query=urlencode(query), fragment="").geturl().rstrip("/")


def _legacy_global_s_strip(url: str) -> str:
    """The rule 2734927 rejected: strip ``s`` on every host."""
    url = url.strip().strip('"').strip("'")
    parsed = urlparse(url)
    tracking = {"s", "t", "utm_source", "utm_medium", "utm_campaign", "si", "fbclid"}
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in tracking]
    return parsed._replace(query=urlencode(query), fragment="").geturl().rstrip("/")


def _legacy_fix_walk(root: Path) -> None:
    """``fix_paths.fix_all_paths`` before ca2611b: no quarantine exception.

    Frontmatter ``target`` / ``type`` moves the note, including notes under
    ``_quarantine``. Empty directories are then removed.
    """
    import os
    import shutil

    for folder in ("garden", "wiki"):
        base = root / folder
        if not base.is_dir():
            continue
        for file_path in list(base.rglob("*.md")):
            if file_path.name == "index.md":
                continue
            text = file_path.read_text(encoding="utf-8")
            target = "machine" if "target: machine" in text else None
            note_type = "concept" if "type: concept" in text else None
            if target == "machine" or folder == "wiki":
                folder_name = "entities" if note_type and note_type.startswith("entity") else "concepts"
                correct = root / "wiki" / folder_name
            else:
                continue
            correct.mkdir(parents=True, exist_ok=True)
            dest = correct / file_path.name
            if file_path.resolve() != dest.resolve():
                if dest.exists():
                    dest.unlink()
                shutil.move(str(file_path), str(dest))
        for dirpath, _, _ in os.walk(base, topdown=False):
            if Path(dirpath) in {root / "garden", root / "wiki", root / "wiki" / "concepts", root / "wiki" / "entities"}:
                continue
            if not os.listdir(dirpath):
                os.rmdir(dirpath)


def test_canonical_url_strips_quotes_utm_and_fragment():
    parsed = CanonicalUrl.parse(' "https://example.com/a/?utm_source=tw&id=9#frag" ')
    assert parsed.identity == "https://example.com/a?id=9"
    assert parsed.query == (("id", "9"),)


def test_x_share_params_collapse_across_host_aliases():
    quoted = CanonicalUrl.parse(
        "'https://Twitter.com/user/status/123?s=52&t=20&utm_campaign=c#x'"
    )
    mobile = CanonicalUrl.parse("https://mobile.twitter.com/user/status/123/")
    plain = CanonicalUrl.parse("https://x.com/user/status/123")
    assert quoted == mobile == plain
    assert quoted.identity == "https://x.com/user/status/123"
    assert quoted.query == ()
    dropped = CanonicalUrl.parse("https://x.com/user/status/123?lang=en")
    assert dropped.query == ()


def test_wordpress_search_is_not_stripped():
    page = CanonicalUrl.parse("https://blog.example.com/?s=zettelkasten&utm_medium=rss&si=1")
    home = CanonicalUrl.parse("https://blog.example.com/")
    assert page.query == (("s", "zettelkasten"),)
    assert page.identity == "https://blog.example.com/?s=zettelkasten"
    assert page.identity != home.identity


def test_percent_decode_once_for_linkedin_path_and_query():
    encoded = CanonicalUrl.parse("https://www.linkedin.com/in/" + quote("홍길동"))
    plain = CanonicalUrl.parse("https://www.linkedin.com/in/홍길동")
    assert encoded.path == "/in/홍길동"
    assert encoded == plain
    twice = CanonicalUrl.parse("https://cdn.example.com/a?title=%2520")
    assert twice.query == (("title", "%20"),)
    assert unquote(twice.query[0][1]) == " "


def test_box_com_is_not_an_x_alias():
    parsed = CanonicalUrl.parse("https://box.com/file?s=keep&utm_source=x")
    assert parsed.host == "box.com"
    assert parsed.query == (("s", "keep"),)
    other = CanonicalUrl.parse("https://foo.twitter.com/a?s=1")
    assert other.host == "foo.twitter.com"
    assert other.query == (("s", "1"),)


def test_legacy_normalizers_fail_the_canonical_contract():
    """The same assertions the new type passes, applied to the old rules."""
    x_share = "https://x.com/user/status/123?s=52"
    alias = "https://twitter.com/user/status/123"
    wordpress = "https://blog.example.com/?s=zettelkasten"
    double = "https://cdn.example.com/a?title=%2520"
    box = "https://box.com/file?s=keep"
    linkedin = "https://www.linkedin.com/in/" + quote("홍길동")

    assert CanonicalUrl.parse(x_share).identity == "https://x.com/user/status/123"
    assert _legacy_pipeline_normalize(x_share) != CanonicalUrl.parse(x_share).identity
    assert _legacy_utils_normalize(alias) != CanonicalUrl.parse(alias).identity
    assert _legacy_pipeline_normalize(double) != CanonicalUrl.parse(double).identity
    assert _legacy_utils_normalize(box) != CanonicalUrl.parse(box).identity
    assert _legacy_utils_normalize(linkedin) != CanonicalUrl.parse(linkedin).identity
    assert _legacy_global_s_strip(wordpress) != CanonicalUrl.parse(wordpress).identity
    assert "s=" not in _legacy_global_s_strip(wordpress)


def test_non_http_url_is_rejected():
    with pytest.raises(ContractError):
        CanonicalUrl.parse("not a url")


def test_placement_spaces_and_quarantine_segment():
    wiki = placement_from_parts(("wiki", "events", "loose.md"))
    assert wiki == Placement(Space.WIKI, "events/loose.md")
    quarantined = placement_from_parts(("wiki", "_quarantine", "concepts", "kept.md"))
    assert quarantined.space is Space.QUARANTINE
    assert quarantined.relative == "concepts/kept.md"
    assert publishable(quarantined) is None
    assert publishable(wiki) == Publishable(Space.WIKI, "events/loose.md")
    with pytest.raises(ContractError, match="quarantine walk"):
        Publishable(Space.QUARANTINE, "kept.md")


def test_publish_fix_does_not_move_quarantine_notes(tmp_path: Path):
    kept = tmp_path / "wiki" / "_quarantine" / "concepts" / "kept.md"
    kept.parent.mkdir(parents=True)
    kept.write_text(_note(), encoding="utf-8")
    empty = tmp_path / "wiki" / "_quarantine" / "unused"
    empty.mkdir()
    loose = tmp_path / "wiki" / "events" / "loose.md"
    loose.parent.mkdir(parents=True)
    loose.write_text(_note(), encoding="utf-8")
    garden_kept = tmp_path / "garden" / "_quarantine" / "personal.md"
    garden_kept.parent.mkdir(parents=True)
    garden_kept.write_text(_note(target="human"), encoding="utf-8")

    apply_publish_fix(tmp_path)

    assert kept.is_file(), "quarantine walk: publish/fix moved a quarantine note back"
    assert not (tmp_path / "wiki" / "concepts" / "kept.md").exists(), (
        "quarantine walk: publish/fix moved a quarantine note back"
    )
    assert empty.is_dir(), "quarantine walk: publish/fix removed a quarantine directory"
    assert garden_kept.is_file(), "quarantine walk: publish/fix moved a quarantine note back"
    assert (tmp_path / "wiki" / "concepts" / "loose.md").is_file()
    assert not loose.exists()


def test_legacy_quarantine_walk_fails_the_same_check(tmp_path: Path):
    """The pre-ca2611b walker trips the quarantine-walk assertion."""
    kept = tmp_path / "wiki" / "_quarantine" / "kept.md"
    kept.parent.mkdir(parents=True)
    kept.write_text(_note(), encoding="utf-8")
    empty = tmp_path / "wiki" / "_quarantine" / "unused"
    empty.mkdir()

    _legacy_fix_walk(tmp_path)

    with pytest.raises(AssertionError, match="quarantine walk"):
        assert kept.is_file(), "quarantine walk: publish/fix moved a quarantine note back"
    assert (tmp_path / "wiki" / "concepts" / "kept.md").is_file()
    assert not empty.exists()


def test_no_conflict_markers_in_contract_sources():
    root = Path(__file__).resolve().parents[1]
    needles = ("<" * 7, "=" * 7, ">" * 7)
    targets = [
        root / "scripts" / "pipeline_contract.py",
        Path(__file__).resolve(),
    ]
    hits: list[str] = []
    for path in targets:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(needles):
                hits.append(f"{path.relative_to(root)}:{number}")
    assert not hits, "conflict-marker: " + ", ".join(hits)
