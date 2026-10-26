"""Sample inputs rendered as a text table.

The snapshot is the pipeline output: FetchOutcome (canonical URL), Placement,
and NoteRecord after publish/fix. A quarantined note that moves back to its
old folder changes ``disk_after`` and adds a Moves row. That is a snapshot diff.
"""

from __future__ import annotations

import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import quote

from inline_snapshot import snapshot

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pipeline_contract import (  # noqa: E402
    CanonicalUrl,
    ContractError,
    apply_publish_fix,
    normalize,
    placement_from_parts,
    publishable,
)

_TWITTER = "https://Twitter.com/user/status/123?s=52&t=20&utm_campaign=c"
_WORDPRESS = "https://blog.example.com/?s=zettelkasten&utm_medium=rss&si=1"
_LINKEDIN = "https://www.linkedin.com/in/" + quote("홍길동")
_PERCENT = "https://cdn.example.com/%2520file?title=%2520"
_BOX = "https://box.com/file?s=keep&utm_source=x"

_FETCHES = [
    ("x-share-twitter", f"'{_TWITTER}#x'"),
    ("x-share-mobile", "https://mobile.twitter.com/user/status/123/"),
    ("x-share-x", "https://x.com/user/status/123"),
    ("wordpress-search", _WORDPRESS),
    ("percent-twice", _PERCENT),
    ("box-not-x", _BOX),
    ("linkedin-ko", _LINKEDIN),
    ("not-substring", "https://foo.twitter.com/a?s=1"),
    ("slash-only", "http://x.com//"),
]

# input path, filename, frontmatter. index.md is the publish/fix skip.
_NOTES = [
    ("wiki/_quarantine/concepts/kept.md", "kept.md", "machine", "concept", _TWITTER, "Kept"),
    ("wiki/events/loose.md", "loose.md", "machine", "concept", _WORDPRESS, "Loose"),
    ("garden/_quarantine/personal.md", "personal.md", "human", "concept", _PERCENT, "Personal"),
    ("garden/drafts/draft.md", "draft.md", "human", "essay", _BOX, "Draft"),
    ("garden/stays.md", "stays.md", "human", "essay", _LINKEDIN, "Stays"),
    ("clippings/article.md", "article.md", "machine", "source", _LINKEDIN, "Article"),
    ("wiki/entities/org.md", "org.md", "machine", "entity-org", "https://example.com/org", "Org"),
    ("wiki/index.md", "index.md", "", "", "", "Index"),
    ("wiki/_quarantine/index.md", "index.md", "", "", "", "Quarantine index"),
]

_DIRECTORIES = [
    "wiki/_quarantine",
    "wiki/_quarantine/unused",
    "wiki/_quarantine/concepts",
    "wiki/events",
    "wiki/concepts",
    "garden/drafts",
    "garden/_quarantine",
    "clippings",
]


def _note(target: str, note_type: str, source: str, title: str) -> str:
    lines = ["---", f"title: {title}"]
    if target:
        lines.append(f"target: {target}")
    if note_type:
        lines.append(f"type: {note_type}")
    if source:
        lines.append(f'source_url: "{source}"')
    lines.extend(["---", "", "body", ""])
    return "\n".join(lines)


def _field(text: str, key: str) -> str:
    prefix = f"{key}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return "-"


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    cells = [headers] + rows
    widths = [max(_display_width(row[i]) for row in cells) for i in range(len(headers))]

    def line(row: list[str]) -> str:
        return " | ".join(_pad(cell, width) for cell, width in zip(row, widths)).rstrip()

    rule = "-+-".join("-" * width for width in widths)
    return "\n".join([line(headers), rule, *[line(row) for row in rows]])


def _query(parsed: CanonicalUrl) -> str:
    if not parsed.query:
        return "-"
    return "&".join(f"{key}={value!r}" for key, value in parsed.query)


def _locate(root: Path, start: str, filename: str, inputs: set[str]) -> str:
    if (root / start).is_file():
        return start
    found = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob(filename)
        if path.is_file()
    )
    extras = [path for path in found if path not in inputs]
    if len(extras) == 1:
        return extras[0]
    return "MISSING(" + ",".join(found) + ")"


def _space(root: Path, rel: str) -> tuple[str, str, str]:
    placement = placement_from_parts(tuple(Path(rel).parts))
    allowed = publishable(placement)
    return (
        placement.space.value,
        placement.relative,
        "no" if allowed is None else "yes",
    )


def render_report() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inputs = {rel for rel, *_rest in _NOTES}
        for rel, _filename, target, note_type, source, title in _NOTES:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_note(target, note_type, source, title), encoding="utf-8")
        (root / "wiki" / "_quarantine" / "unused").mkdir(parents=True, exist_ok=True)

        moves = sorted(apply_publish_fix(root))

        fetch_rows = []
        for name, raw in _FETCHES:
            parsed = CanonicalUrl.parse(raw)
            fetch_rows.append([name, normalize(raw), repr(parsed.path), _query(parsed)])

        placement_rows = []
        note_rows = []
        for rel, filename, _target, _note_type, _source, title in _NOTES:
            space, relative, allowed = _space(root, rel)
            disk_after = _locate(root, rel, filename, inputs)
            placement_rows.append([title, rel, space, relative, allowed, disk_after])
            final_space = final_relative = "-"
            if disk_after.startswith("MISSING"):
                final_label = disk_after
            else:
                final_space, final_relative, _allowed = _space(root, disk_after)
                final_label = disk_after
            text = (root / disk_after).read_text(encoding="utf-8") if not disk_after.startswith("MISSING") else ""
            source_raw = _field(text, "source_url")
            try:
                source_id = normalize(source_raw) if source_raw != "-" else "-"
            except ContractError:
                source_id = source_raw
            note_rows.append(
                [
                    filename,
                    title,
                    final_label,
                    final_space,
                    final_relative,
                    _field(text, "target"),
                    _field(text, "type"),
                    source_id,
                ]
            )

        directory_rows = [
            [rel, "present" if (root / rel).is_dir() else "absent"] for rel in _DIRECTORIES
        ]
        move_rows = [[src, dest] for src, dest in moves] or [["-", "-"]]

    sections = [
        "pipeline sample report",
        "space = wiki | garden | clippings | quarantine. disk_after is the path after publish/fix.",
        "publishable=no must keep disk_after equal to input. A quarantine return shows up here.",
        "",
        "FetchOutcome",
        _table(["sample", "identity", "path", "query"], fetch_rows),
        "",
        "Placement",
        _table(
            ["note", "input", "space", "relative", "publishable", "disk_after"],
            placement_rows,
        ),
        "",
        "NoteRecord",
        _table(
            ["file", "note", "disk", "space", "relative", "target", "type", "source"],
            note_rows,
        ),
        "",
        "Moves",
        _table(["source", "dest"], move_rows),
        "",
        "Directories",
        _table(["path", "state"], directory_rows),
        "",
    ]
    return "\n".join(sections)


def test_sample_pipeline_report():
    assert render_report() == snapshot("""\
pipeline sample report
space = wiki | garden | clippings | quarantine. disk_after is the path after publish/fix.
publishable=no must keep disk_after equal to input. A quarantine return shows up here.

FetchOutcome
sample           | identity                                      | path               | query
-----------------+-----------------------------------------------+--------------------+-----------------
x-share-twitter  | https://x.com/user/status/123                 | '/user/status/123' | -
x-share-mobile   | https://x.com/user/status/123                 | '/user/status/123' | -
x-share-x        | https://x.com/user/status/123                 | '/user/status/123' | -
wordpress-search | https://blog.example.com/?s=zettelkasten      | '/'                | s='zettelkasten'
percent-twice    | https://cdn.example.com/%2520file?title=%2520 | '/%20file'         | title='%20'
box-not-x        | https://box.com/file?s=keep                   | '/file'            | s='keep'
linkedin-ko      | https://www.linkedin.com/in/홍길동            | '/in/홍길동'       | -
not-substring    | https://foo.twitter.com/a?s=1                 | '/a'               | s='1'
slash-only       | http://x.com                                  | '/'                | -

Placement
note             | input                             | space      | relative         | publishable | disk_after
-----------------+-----------------------------------+------------+------------------+-------------+----------------------------------
Kept             | wiki/_quarantine/concepts/kept.md | quarantine | concepts/kept.md | no          | wiki/_quarantine/concepts/kept.md
Loose            | wiki/events/loose.md              | wiki       | events/loose.md  | yes         | wiki/concepts/loose.md
Personal         | garden/_quarantine/personal.md    | quarantine | personal.md      | no          | garden/_quarantine/personal.md
Draft            | garden/drafts/draft.md            | garden     | drafts/draft.md  | yes         | garden/draft.md
Stays            | garden/stays.md                   | garden     | stays.md         | yes         | garden/stays.md
Article          | clippings/article.md              | clippings  | article.md       | yes         | clippings/article.md
Org              | wiki/entities/org.md              | wiki       | entities/org.md  | yes         | wiki/entities/org.md
Index            | wiki/index.md                     | wiki       | index.md         | yes         | wiki/index.md
Quarantine index | wiki/_quarantine/index.md         | quarantine | index.md         | no          | wiki/_quarantine/index.md

NoteRecord
file        | note             | disk                              | space      | relative          | target  | type       | source
------------+------------------+-----------------------------------+------------+-------------------+---------+------------+----------------------------------------------
kept.md     | Kept             | wiki/_quarantine/concepts/kept.md | quarantine | concepts/kept.md  | machine | concept    | https://x.com/user/status/123
loose.md    | Loose            | wiki/concepts/loose.md            | wiki       | concepts/loose.md | machine | concept    | https://blog.example.com/?s=zettelkasten
personal.md | Personal         | garden/_quarantine/personal.md    | quarantine | personal.md       | human   | concept    | https://cdn.example.com/%2520file?title=%2520
draft.md    | Draft            | garden/draft.md                   | garden     | draft.md          | human   | essay      | https://box.com/file?s=keep
stays.md    | Stays            | garden/stays.md                   | garden     | stays.md          | human   | essay      | https://www.linkedin.com/in/홍길동
article.md  | Article          | clippings/article.md              | clippings  | article.md        | machine | source     | https://www.linkedin.com/in/홍길동
org.md      | Org              | wiki/entities/org.md              | wiki       | entities/org.md   | machine | entity-org | https://example.com/org
index.md    | Index            | wiki/index.md                     | wiki       | index.md          | -       | -          | -
index.md    | Quarantine index | wiki/_quarantine/index.md         | quarantine | index.md          | -       | -          | -

Moves
source                 | dest
-----------------------+-----------------------
garden/drafts/draft.md | garden/draft.md
wiki/events/loose.md   | wiki/concepts/loose.md

Directories
path                      | state
--------------------------+--------
wiki/_quarantine          | present
wiki/_quarantine/unused   | present
wiki/_quarantine/concepts | present
wiki/events               | present
wiki/concepts             | present
garden/drafts             | present
garden/_quarantine        | present
clippings                 | present
""")
