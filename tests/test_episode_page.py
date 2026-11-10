"""Hermod's page helper, run against the artifact Munnin's contract owns.

This module is the *parity* half of the design: Hermod houses its own implementation so the two
servers stay independently deployable, and the shared vector file is what keeps that honest. The
checksum below is the same one Munnin's ``tests/business_services/test_episode_page.py`` pins, so a
change to one copy that is not made to the other fails loudly instead of drifting quietly.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from hermod.business_services.episode_page import count_groups, page_body

#: sha256 of the shared artifact — deliberately the same value pinned in Munnin's test module.
VECTORS_SHA256 = "cbaeac95ad6bc0de9dd48a7519a52e6ed802313b96be68269b622a6d5b588a4a"
VECTORS_PATH = pathlib.Path(__file__).resolve().parent / "conformance" / (
    "episode_page_vectors.json"
)

_ARTIFACT = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
VECTORS = _ARTIFACT["vectors"]
OK_VECTORS = [vector for vector in VECTORS if "expected" in vector]
ERROR_VECTORS = [vector for vector in VECTORS if "error_contains" in vector]


def _call(vector: dict):
    return page_body(
        vector["content"],
        cursor=vector["cursor"],
        limit=vector["limit"],
        max_body_bytes=vector["max_body_bytes"],
        split=vector["splittable"],
    )


def test_the_shared_artifact_matches_the_checksum_munnin_pins() -> None:
    assert hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest() == VECTORS_SHA256


@pytest.mark.parametrize("vector", OK_VECTORS, ids=[v["name"] for v in OK_VECTORS])
def test_vector(vector: dict) -> None:
    page = _call(vector)
    expected = vector["expected"]
    assert page.content == expected["content"]
    assert page.blocks_returned == expected["blocks_returned"]
    assert page.blocks_total == expected["blocks_total"]
    assert page.next_cursor == expected["next_cursor"]


@pytest.mark.parametrize("vector", ERROR_VECTORS, ids=[v["name"] for v in ERROR_VECTORS])
def test_vector_refuses(vector: dict) -> None:
    with pytest.raises(ValueError) as caught:
        _call(vector)
    assert vector["error_contains"] in str(caught.value)


THIRTY = "".join(
    f"### 2026-01-01 10.{index:02d} - g{index:02d}\n\nbody {index:02d}\n" for index in range(30)
)
_BLOCK = re.compile(r"### \d{4}-\d{2}-\d{2} \d{2}\.\d{2} - g\d{2}\n\nbody \d{2}\n")


def test_a_traversal_terminates_and_covers_every_block_exactly_once() -> None:
    pages = []
    cursor = None
    for _ in range(1000):
        page = page_body(THIRTY, cursor=cursor, limit=3)
        pages.append(page)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        raise AssertionError("traversal never terminated")
    assert len(pages) == 10
    assert all(page.blocks_total == 30 for page in pages)
    joined = "".join(page.content for page in pages)
    assert joined == THIRTY
    assert len(_BLOCK.findall(joined)) == 30


def test_a_prepended_block_does_not_shift_the_cursor() -> None:
    first = page_body(THIRTY, limit=3)
    newer = "### 2026-01-02 08.00 - arrived between reads\n\nnew body\n"
    second = page_body(newer + THIRTY, cursor=first.next_cursor, limit=3)
    assert second.content.startswith("### 2026-01-01 10.03")
    assert newer not in second.content


def test_count_groups_agrees_with_the_page() -> None:
    assert count_groups(THIRTY) == page_body(THIRTY, limit=1).blocks_total == 30
    assert count_groups(THIRTY, split=False) == 1
    assert count_groups("") == 1


@pytest.mark.parametrize("limit", [0, -2, True, "3"])
def test_a_bad_limit_is_refused(limit) -> None:
    with pytest.raises(ValueError):
        page_body(THIRTY, limit=limit)
