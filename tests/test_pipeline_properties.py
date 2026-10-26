"""Property tests for CanonicalUrl.normalize.

``normalize(normalize(x)) == normalize(x)``. ``x.com`` and ``twitter.com``
aliases share one identity. A percent-escape is decoded once, so ``%2520``
stays ``%20`` and does not become a space.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlencode

from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pipeline_contract import CanonicalUrl, normalize  # noqa: E402

_SETTINGS = settings(max_examples=100, deadline=None, derandomize=True)

_ALIASES = [
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
]

_HOSTS = _ALIASES + [
    "example.com",
    "blog.example.com",
    "box.com",
    "foo.twitter.com",
    "cdn.example.com",
    "www.linkedin.com",
]

_SEGMENT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="/?#"),
    max_size=8,
)
_KEY = st.one_of(
    st.sampled_from(["s", "t", "utm_source", "utm_medium", "id", "title", "si", "fbclid", "ref", "q"]),
    st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,8}", fullmatch=True),
)
_VALUE = st.text(
    alphabet=st.characters(codec="utf-8", min_codepoint=32, max_codepoint=0xFFFF),
    max_size=12,
)
_PAIRS = st.lists(st.tuples(_KEY, _VALUE), max_size=4)
_PAYLOAD = st.text(
    alphabet=st.characters(codec="utf-8", min_codepoint=32, max_codepoint=0xFFFF),
    max_size=16,
)


@st.composite
def _http_urls(draw) -> str:
    scheme = draw(st.sampled_from(["http", "https", "HTTP", "HTTPS"]))
    host = draw(st.sampled_from(_HOSTS))
    port = draw(st.one_of(st.none(), st.sampled_from([80, 443, 8443])))
    segments = draw(st.lists(_SEGMENT, max_size=3))
    path = "/".join(quote(segment, safe="") for segment in segments)
    query = urlencode(draw(_PAIRS))
    fragment = draw(st.one_of(st.just(""), st.text(alphabet="abcXYZ%20", max_size=6)))
    netloc = host if port is None else f"{host}:{port}"
    url = f"{scheme}://{netloc}/{path}"
    if query:
        url += "?" + query
    if fragment:
        url += "#" + fragment
    wrap = draw(st.sampled_from(["none", "quotes", "single", "spaces"]))
    if wrap == "quotes":
        return f'"{url}"'
    if wrap == "single":
        return f"'{url}'"
    if wrap == "spaces":
        return f"  {url}  "
    return url


def _once_decoded_path(double_encoded_segment: str) -> str:
    """One ``unquote`` of ``/{segment}``, then the contract's slash trim."""
    decoded = unquote("/" + double_encoded_segment).rstrip("/")
    return "/" if decoded in {"", "/"} else decoded


@_SETTINGS
@given(url=_http_urls())
@example("http://x.com//")
@example("https://x.com///")
@example("https://cdn.example.com/%2520file?title=%2520")
@example("https://cdn.example.com/a%C2%A0")
@example("https://cdn.example.com/a%C2%85")
@example("https://cdn.example.com/a%23b%3Fc")
@example("https://www.linkedin.com/in/" + quote("홍길동"))
@example(' "https://Twitter.com/user/status/123?s=52&t=20&utm_campaign=c#x" ')
@example("https://blog.example.com//?s=zettelkasten&utm_medium=rss")
@example("https://box.com/file?s=keep&utm_source=x")
def test_normalize_is_idempotent(url: str):
    assert normalize(normalize(url)) == normalize(url)
    assert CanonicalUrl.parse(normalize(url)) == CanonicalUrl.parse(url)


@_SETTINGS
@given(
    scheme=st.sampled_from(["http", "https"]),
    left=st.sampled_from(_ALIASES),
    right=st.sampled_from(_ALIASES),
    segments=st.lists(_SEGMENT, max_size=3),
    pairs=_PAIRS,
    port=st.one_of(st.none(), st.sampled_from([80, 443, 8443])),
)
@example("https", "twitter.com", "x.com", ["user", "status", "123"], [("s", "52"), ("t", "20")], None)
@example("https", "mobile.twitter.com", "www.x.com", ["user", "status", "123"], [("utm_source", "tw")], None)
def test_x_and_twitter_aliases_canonicalize_to_the_same_value(
    scheme, left, right, segments, pairs, port
):
    path = "/".join(quote(segment, safe="") for segment in segments)
    query = urlencode(pairs)

    def build(host: str) -> str:
        netloc = host if port is None else f"{host}:{port}"
        url = f"{scheme}://{netloc}/{path}"
        if query:
            url += "?" + query
        return url

    assert normalize(build(left)) == normalize(build(right))
    assert CanonicalUrl.parse(build(left)) == CanonicalUrl.parse(build(right))


@_SETTINGS
@given(prefix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=4))
@example("bo")  # box.com contains the letters x.com but is not X
def test_host_containing_x_com_is_not_an_alias(prefix: str):
    host = f"{prefix}x.com"
    parsed = CanonicalUrl.parse(f"https://{host}/file?s=keep&utm_source=tw")
    assert parsed.host == host
    assert parsed.query == (("s", "keep"),)
    assert normalize(f"https://{host}/file?s=keep") != normalize("https://x.com/file?s=keep")


@_SETTINGS
@given(label=st.from_regex(r"[a-z]{2,8}", fullmatch=True))
@example("foo")
def test_extra_twitter_label_is_not_an_alias(label: str):
    assume(label not in {"www", "mobile"})
    host = f"{label}.twitter.com"
    parsed = CanonicalUrl.parse(f"https://{host}/a?s=1&t=2")
    assert parsed.host == host
    assert parsed.query == (("s", "1"), ("t", "2"))


@_SETTINGS
@given(payload=_PAYLOAD)
@example(" ")
@example("%20")
@example("홍길동")
@example("a/b")
@example("#hash?")
@example("100%")
def test_percent_encoding_is_decoded_once(payload: str):
    once = quote(payload, safe="")
    twice = quote(once, safe="")
    url = f"https://cdn.example.com/{twice}?title={twice}"
    parsed = CanonicalUrl.parse(url)

    assert parsed.query == (("title", once),)
    assert parsed.path == _once_decoded_path(twice)
    if unquote(once) != once:
        assert parsed.query[0][1] != unquote(once)
        assert parsed.path != _once_decoded_path(once)
    assert CanonicalUrl.parse(parsed.identity) == parsed
