"""Bounded pages of a rolling episode body, cut at timestamp groups.

This is Hermod's own copy of the page rule Munnin applies to the same rows. It is deliberately
housed here rather than imported: the two servers deploy independently, and Hermod reads the
store directly (ADR-029), so an RPC or a shared runtime package would buy a dependency the
architecture does not otherwise have. What keeps the two honest is a shared conformance
artifact — ``tests/conformance/episode_page_vectors.json``, transferred verbatim from Munnin's
contract — whose checksum both test modules pin. Two implementations, one contract.

The rule, in one paragraph: an episode's body is a newest-first stack of sub-episode blocks,
each opened by an H3 heading carrying a ``YYYY-MM-DD HH.MM`` timestamp. A **timestamp group** —
every block in the record sharing one moment — is the indivisible page unit. The cursor is the
group's own timestamp, so a block prepended between two reads moves nothing. The first group of
a page is always emitted whole, even above the byte ceiling, and later groups are added only
while they fit; nothing is ever split, dropped or re-headed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: An ATX H3 heading (up to three leading spaces, as CommonMark allows). ``####`` does not match.
_HEADING = re.compile(r"^ {0,3}###[ \t]+(?P<text>.*?)[ \t]*$")

#: A boundary: the heading text opens with a timestamp. Captured verbatim — the spelling is what
#: a cursor returns and what a caller sends back — and required to end the heading text or be
#: followed by a space, so ``2026-11-10 10.05-untitled`` is not silently promoted to a boundary.
_OPENING_STAMP = re.compile(
    r"^\[?(?P<stamp>"
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[ \t]+"
    r"(?P<hour>\d{2})[.:](?P<minute>\d{2})"
    r")\]?(?=\s|$)"
)

#: A cursor: the same stamp, optionally carrying the historical brackets, matched whole.
_CURSOR = re.compile(
    r"^\[?"
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[ \t]+"
    r"(?P<hour>\d{2})[.:](?P<minute>\d{2})"
    r"\]?$"
)

#: A fenced-code marker; a boundary-looking line inside a fence is code, not a boundary.
_FENCE = re.compile(r"^ {0,3}(?P<run>`{3,}|~{3,})")

Moment = tuple[int, int, int, int, int]

#: One page of an episode body may hold at most this many UTF-8 bytes of content.
#:
#: The same contract and the same evidence as Munnin's setting, established 2026-11-10: the
#: tightest client cap this fleet records is Claude Code's 10,000-token Read cap (``SETUP.md`` in
#: the core framework), the store converts at 4 characters per token, and a measured
#: single-record response adds ~338 bytes of JSON envelope around the body. 32,000 is 80% of
#: that 40,000-byte cap, leaving room for the envelope, JSON escaping and the chars/4 heuristic
#: undercounting emoji-dense text. It is a content budget for one record, not a promise that the
#: whole awakening fits — the lanes, indexes, context and maps sit outside it.
DEFAULT_MAX_BODY_BYTES = 32_000


@dataclass(frozen=True)
class EpisodePage:
    """One page: the text plus what a caller needs to know about the rest of the record.

    ``blocks_returned`` and ``blocks_total`` are both in timestamp groups. They compare *this
    page* with *the whole record*, which is why they cannot answer "is there more?" — that is
    ``next_cursor`` alone, since a final page can return fewer than the total and still be done.
    """

    content: str
    blocks_returned: int
    blocks_total: int
    next_cursor: str | None

    def fields(self) -> dict[str, object]:
        """The page fields as they sit beside ``content`` in the awakening payload."""
        return {
            "blocks_returned": self.blocks_returned,
            "blocks_total": self.blocks_total,
            "next_cursor": self.next_cursor,
        }


@dataclass(frozen=True)
class _Block:
    """One sub-episode block: its slice of the original text, and the moment it names."""

    moment: Moment | None
    stamp: str | None
    text: str


def _moment(year: str, month: str, day: str, hour: str, minute: str) -> Moment | None:
    """The moment those five numbers name, or ``None`` when they name no real minute.

    Range checking is not pedantry: a heading such as ``### 2026-13-45 99.99`` is prose, and
    accepting it would mint a cursor that can never be found again.
    """
    month_number = int(month)
    day_number = int(day)
    if not 1 <= month_number <= 12:
        return None
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        return None
    try:
        # ``date`` knows leap years; the tuple above does not.
        date(int(year), month_number, day_number)
    except ValueError:
        return None
    return (int(year), month_number, day_number, int(hour), int(minute))


def _block_moment(text: str) -> tuple[Moment, str] | None:
    """The moment a heading opens with, and its verbatim stamp, or ``None``."""
    found = _OPENING_STAMP.match(text)
    if found is None:
        return None
    moment = _moment(
        found["year"], found["month"], found["day"], found["hour"], found["minute"]
    )
    return (moment, found["stamp"]) if moment is not None else None


def _cursor_moment(cursor: str) -> Moment:
    """Parse a caller's cursor, refusing anything that is not one block timestamp."""
    found = _CURSOR.match(cursor.strip())
    if found is None:
        raise ValueError(
            f"invalid cursor {cursor!r}: expected a timestamp from this record's own blocks, "
            "such as '2026-11-10 10.05'"
        )
    moment = _moment(
        found["year"], found["month"], found["day"], found["hour"], found["minute"]
    )
    if moment is None:
        raise ValueError(f"invalid cursor {cursor!r}: no such date and time")
    return moment


def _blocks(content: str, *, split: bool) -> list[_Block]:
    """Cut a body into its sub-episode blocks, or return it as one block.

    The preamble — whatever precedes the first boundary — rides with the first block: it is the
    record's own opening and there is nowhere else for it to go. A body with no usable boundary
    is one atomic block, and ``split=False`` says the same thing for a record type that is not an
    episode, even when its text happens to contain heading-shaped lines.
    """
    if not split:
        return [_Block(moment=None, stamp=None, text=content)]

    lines = content.splitlines(keepends=True)
    starts: list[tuple[int, Moment, str]] = []
    open_fence: str | None = None
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        fence = _FENCE.match(line)
        if open_fence is not None:
            if fence is not None and fence["run"][0] == open_fence[0] and (
                len(fence["run"]) >= len(open_fence)
            ):
                open_fence = None
            continue
        if fence is not None:
            open_fence = fence["run"]
            continue
        heading = _HEADING.match(line)
        if heading is None:
            continue
        found = _block_moment(heading["text"].strip())
        if found is not None:
            moment, stamp = found
            starts.append((index, moment, stamp))

    if not starts:
        return [_Block(moment=None, stamp=None, text=content)]

    blocks: list[_Block] = []
    for position, (index, moment, stamp) in enumerate(starts):
        begin = 0 if position == 0 else index
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        blocks.append(_Block(moment=moment, stamp=stamp, text="".join(lines[begin:end])))
    return blocks


def _grouped(blocks: list[_Block]) -> list[_Block]:
    """Fold the blocks into timestamp groups, keeping first-occurrence and in-group order.

    Grouping is what makes a timestamp collision a non-problem: two blocks written in the same
    minute are one page unit, so no fingerprint, persisted id or ordinal is needed to tell them
    apart. An untimed body (one block with no moment) is returned as it stands.
    """
    order: list[Moment] = []
    parts: dict[Moment, list[str]] = {}
    stamps: dict[Moment, str] = {}
    loose: list[_Block] = []
    for block in blocks:
        if block.moment is None:
            loose.append(block)
            continue
        if block.moment not in parts:
            order.append(block.moment)
            parts[block.moment] = []
            stamps[block.moment] = block.stamp  # type: ignore[assignment]
        parts[block.moment].append(block.text)
    return loose + [
        _Block(moment=moment, stamp=stamps[moment], text="".join(parts[moment]))
        for moment in order
    ]


def count_groups(content: str, *, split: bool = True) -> int:
    """How many page units a body holds — the ``blocks_total`` a listing needs."""
    return len(_grouped(_blocks(content or "", split=split)))


def page_body(
    content: str,
    *,
    cursor: str | None = None,
    limit: int | None = None,
    max_body_bytes: int | None = None,
    split: bool = True,
) -> EpisodePage:
    """One page of ``content``: the whole body by default, bounded on request.

    ``cursor`` resumes *at* the group it names, inclusive, so a caller feeds a previous page's
    ``next_cursor`` straight back. An unknown or malformed cursor or a nonpositive ``limit``
    raises ``ValueError`` — answering with page one would hide a continuation that failed, and
    there is no ordinal to fall back to.

    ``max_body_bytes`` limits the page's UTF-8 content bytes, not the serialized response. One
    group is indivisible, so the first selected group is emitted whatever its size.

    A read that emits every group from the first one returns the stored body byte for byte:
    grouping may move non-adjacent equal timestamps together, which is acceptable only for a
    partial page.
    """
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError(f"invalid limit {limit!r}: expected a positive number of blocks")
    if max_body_bytes is not None and (
        isinstance(max_body_bytes, bool)
        or not isinstance(max_body_bytes, int)
        or max_body_bytes < 1
    ):
        raise ValueError(f"invalid max_body_bytes {max_body_bytes!r}: expected a positive number")

    groups = _grouped(_blocks(content or "", split=split))
    begin = 0
    if cursor is not None:
        wanted = _cursor_moment(cursor)
        try:
            begin = next(
                index for index, group in enumerate(groups) if group.moment == wanted
            )
        except StopIteration:
            raise ValueError(
                f"cursor {cursor!r} does not match any timestamp block in this record"
            ) from None

    selected = groups[begin:]
    if limit is not None:
        selected = selected[:limit]

    page: list[_Block] = []
    taken = 0
    for position, group in enumerate(selected):
        size = len(group.text.encode("utf-8"))
        if position and max_body_bytes is not None and taken + size > max_body_bytes:
            break
        page.append(group)
        taken += size

    complete = begin == 0 and len(page) == len(groups)
    if complete:
        rendered = content or ""
    else:
        rendered = "".join(group.text for group in page)
    following = groups[begin + len(page)] if begin + len(page) < len(groups) else None
    return EpisodePage(
        content=rendered,
        blocks_returned=len(page),
        blocks_total=len(groups),
        next_cursor=following.stamp if following is not None else None,
    )
