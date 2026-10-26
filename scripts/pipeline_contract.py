"""Typed boundary for URL identity and note placement.

This is not a pipeline rewrite. ``scripts/pipeline.py`` still has its own
normalizer. Callers that need the contract import this module.

Historical bugs this module is the type for:

- 6e81f0e: wrapping quotes and ``utm_*`` made the same URL look new.
- 4fc8d73 / bafc09a: LinkedIn paths must be percent-decoded once. Decoding
  again turns ``%2520`` into a space.
- 2734927: ``?s=`` is an X share parameter and a WordPress search query.
  Stripping it globally collapsed search URLs onto the site root.
- ca2611b: ``fix_paths.py`` walked ``_quarantine`` and moved 14 notes back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit


class ContractError(ValueError):
    """A value cannot be represented by the contract."""


class Space(Enum):
    WIKI = "wiki"
    GARDEN = "garden"
    CLIPPINGS = "clippings"
    QUARANTINE = "quarantine"


# Exact host match. ``"x.com" in host`` also matches ``box.com``.
_HOST_ALIASES = {
    "x.com": "x.com",
    "www.x.com": "x.com",
    "mobile.x.com": "x.com",
    "twitter.com": "x.com",
    "www.twitter.com": "x.com",
    "mobile.twitter.com": "x.com",
}

# Keyed by the alias-resolved host. ``None`` is not used: a missing host
# means "drop tracking keys, keep every other key" so WordPress ``s`` survives.
# An empty allowlist means the query is not part of identity (X status URLs).
_QUERY_ALLOWLIST: dict[str, frozenset[str]] = {
    "x.com": frozenset(),
}

_TRACKING_EXACT = frozenset({"si", "fbclid", "gclid", "ref"})

_TOP_LEVEL = {
    "wiki": Space.WIKI,
    "garden": Space.GARDEN,
    "clippings": Space.CLIPPINGS,
}


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_EXACT


def canonical_host(hostname: str) -> str:
    host = hostname.lower().rstrip(".")
    return _HOST_ALIASES.get(host, host)


def _strip_wrapping_quotes(raw: str) -> str:
    text = raw.strip().strip('"').strip("'").strip()
    return text


def _encode_identity_path(path: str) -> str:
    """Percent-encode path characters that a second parse would reinterpret.

    Non-ASCII letters stay decoded. A LinkedIn slug stays readable, and the
    identity string does not collapse onto the legacy normalizer, which leaves
    the original percent-encoding in place. ``%``, ``#``, ``?``, and whitespace
    cannot stay raw: ``%20`` would become a space, ``#`` / ``?`` would be read
    as a fragment or a query, and ``str.strip`` would eat a trailing
    non-breaking space.
    """
    encoded: list[str] = []
    for char in path:
        if char in "-._~/" or (char.isascii() and char.isalnum()):
            encoded.append(char)
        elif ord(char) > 126 and not char.isspace():
            encoded.append(char)
        else:
            encoded.append(quote(char, safe=""))
    return "".join(encoded)


def _normalize_path(path: str) -> str:
    """Percent-decode once and drop a trailing slash.

    ``//`` and ``/`` are the same path. ``rstrip`` turns a slash-only path
    into ``""``, and that empty string parses back as ``/``.
    """
    decoded = unquote(path).rstrip("/")
    if decoded in {"", "/"}:
        return "/"
    return decoded


@dataclass(frozen=True, slots=True)
class CanonicalUrl:
    """One identity for a URL.

    Host aliases collapse before the query allowlist is applied, so
    ``twitter.com`` and ``x.com`` share the X allowlist. The path and query
    are percent-decoded once: ``parse_qsl`` decodes the query, and the raw
    string is not passed through ``unquote`` first. ``identity`` percent-encodes
    the stored path so a second parse does not treat leftover ``%`` escapes
    as new sequences.
    """

    scheme: str
    host: str
    path: str
    query: tuple[tuple[str, str], ...]

    @classmethod
    def parse(cls, raw: str) -> CanonicalUrl:
        text = _strip_wrapping_quotes(raw)
        parts = urlsplit(text)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ContractError(f"not an http(s) url: {raw!r}")
        host = canonical_host(parts.hostname)
        path = _normalize_path(parts.path)
        allow = _QUERY_ALLOWLIST.get(host)
        kept: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if _is_tracking(key):
                continue
            if allow is not None and key.lower() not in allow:
                continue
            kept.append((key.lower(), value))
        netloc = host
        if parts.port not in (None, 80, 443):
            netloc = f"{host}:{parts.port}"
        return cls(parts.scheme.lower(), netloc, path, tuple(kept))

    @property
    def identity(self) -> str:
        query = urlencode(self.query)
        path = _encode_identity_path(self.path)
        if path == "/":
            base = f"{self.scheme}://{self.host}/" if query else f"{self.scheme}://{self.host}"
        else:
            base = f"{self.scheme}://{self.host}{path}"
        if query:
            return f"{base}?{query}"
        return base


def normalize(raw: str) -> str:
    """Canonical identity string for ``raw``.

    ``normalize(normalize(raw)) == normalize(raw)``. ``x.com`` and
    ``twitter.com`` aliases share one value. Percent-escapes are decoded once.
    """
    return CanonicalUrl.parse(raw).identity


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a note lives. ``space`` is the value, not a directory name."""

    space: Space
    relative: str

    def __post_init__(self) -> None:
        if not isinstance(self.space, Space):
            raise ContractError(f"space must be wiki|garden|clippings|quarantine, got {self.space!r}")
        if not self.relative or self.relative.startswith("/") or ".." in Path(self.relative).parts:
            raise ContractError(f"unsafe relative path: {self.relative!r}")
        if "_quarantine" in Path(self.relative).parts:
            raise ContractError("quarantine is a space, not a path segment")


@dataclass(frozen=True, slots=True)
class Publishable:
    """A placement a publish/fix writer may move.

    Constructing this with ``Space.QUARANTINE`` is the quarantine walk.
    """

    space: Space
    relative: str

    def __post_init__(self) -> None:
        if self.space is Space.QUARANTINE:
            raise ContractError(
                "quarantine walk: publish/fix cannot accept a quarantine placement"
            )
        Placement(self.space, self.relative)


def placement_from_parts(parts: tuple[str, ...]) -> Placement:
    """Parse a vault-relative path into a Placement.

    ``_quarantine`` is read here only, as the on-disk spelling of
    ``Space.QUARANTINE``. Callers after this function branch on ``space``.
    """
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ContractError(f"unsafe path: {parts!r}")
    if "_quarantine" in parts:
        index = parts.index("_quarantine")
        relative = "/".join(parts[index + 1 :])
        if not relative:
            raise ContractError("quarantine directory has no note")
        return Placement(Space.QUARANTINE, relative)
    space = _TOP_LEVEL.get(parts[0])
    if space is None:
        raise ContractError(f"unknown space: {parts[0]!r}")
    relative = "/".join(parts[1:])
    if not relative:
        raise ContractError(f"directory is not a note: {parts!r}")
    return Placement(space, relative)


def publishable(placement: Placement) -> Publishable | None:
    if placement.space is Space.QUARANTINE:
        return None
    return Publishable(placement.space, placement.relative)


def _frontmatter_value(text: str, key: str) -> str | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    match = re.search(rf"^{key}:\s*(.+)$", parts[1], re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'")


def fix_destination(
    note: Publishable, *, target: str | None, note_type: str | None
) -> Publishable:
    """Directory a publish/fix pass may move a publishable note into.

    Clippings stay put. Wiki notes land in ``concepts`` or ``entities``.
    ``target: human`` lands in the garden. Quarantine cannot be passed in.
    """
    if note.space is Space.CLIPPINGS:
        return note
    name = Path(note.relative).name
    if target == "human":
        return Publishable(Space.GARDEN, name)
    if target == "machine" or note.space is Space.WIKI:
        folder = "entities" if note_type and note_type.startswith("entity") else "concepts"
        return Publishable(Space.WIKI, f"{folder}/{name}")
    return note


def apply_publish_fix(root: Path) -> list[tuple[str, str]]:
    """Move misplaced wiki/garden notes. Quarantine notes stay where they are.

    Returns ``(source, dest)`` paths relative to ``root``. Does not delete
    empty directories: the old cleanup removed an emptied ``_quarantine``.
    """
    moves: list[tuple[str, str]] = []
    for folder in ("wiki", "garden", "clippings"):
        base = root / folder
        if not base.is_dir():
            continue
        for file_path in list(base.rglob("*.md")):
            if file_path.name == "index.md" or not file_path.is_file():
                continue
            placement = placement_from_parts(file_path.relative_to(root).parts)
            note = publishable(placement)
            if note is None:
                continue
            text = file_path.read_text(encoding="utf-8", errors="replace")
            dest_note = fix_destination(
                note,
                target=_frontmatter_value(text, "target"),
                note_type=_frontmatter_value(text, "type"),
            )
            dest = root / dest_note.space.value / dest_note.relative
            if dest.resolve() == file_path.resolve():
                continue
            if dest.exists():
                raise ContractError(f"publish/fix destination already exists: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            file_path.rename(dest)
            moves.append(
                (
                    str(file_path.relative_to(root)),
                    str(dest.relative_to(root)),
                )
            )
    return moves
