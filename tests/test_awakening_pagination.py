"""The coding awakening's episode rows: addressable pages, not whole rolling bodies.

Hermod reads Munnin's episodes directly (ADR-029), so this is where the two-call awakening meets
pagination. The contract these tests hold: every emitted row names its ``uuid`` (or the page cannot
be continued), carries the page counts, and resumes at exactly the timestamp its cursor names.

PostgreSQL only, for the same reason the rest of the store contract is: it is the only engine
Hermod reads. The suite skips without ``HERMOD_PG_TEST_URL``.
"""

from __future__ import annotations

import os

import pytest

from hermod.business_services.awakening_service import AwakeningService
from hermod.business_services.episode_page import page_body
from hermod.data_repositories.record_project_repository import (
    EPISODE_TYPE,
    RecordProjectRepository,
)
from tests.valaskjalf_schema import make_store

USER = "alvi"
AGENT = "meta"
OTHER_AGENT = "invintiry"

pytestmark = pytest.mark.postgres


def _blocks(count: int, *, day: int = 10) -> str:
    """A rolling episode body: ``count`` sub-episodes, newest first."""
    return "".join(
        f"### 2026-11-{day:02d} 10.{index:02d} - block {index}\n\nbody {index}\n"
        for index in reversed(range(count))
    )


def _episode(
    store: RecordProjectRepository,
    *,
    uuid: str,
    title: str,
    project: str | None,
    body: str,
    agent: str = AGENT,
    created: str = "2026-11-10T00:00:00+00:00",
    archived: str | None = None,
    deleted: str | None = None,
) -> None:
    """Seed one episode row the way Munnin writes it (Hermod has no writer for these)."""
    with store._conn() as conn, conn.cursor() as cur:  # noqa: SLF001 â€” the fixture is the seam
        cur.execute(
            "INSERT INTO memory_record (uuid, user_id, agent_id, record_type, project, title, "
            "tags, created_date, modified_date, archived_date, deleted_date, full_content, "
            "content_summary) VALUES (%s, %s, %s, %s, %s, %s, '[]', %s, %s, %s, %s, %s, %s)",
            (
                uuid, USER, agent, EPISODE_TYPE, project, title,
                created, created, archived, deleted, body, f"{title} summary",
            ),
        )


@pytest.fixture
def url() -> str:
    """The disposable test store, reset so every test starts from a known schema.

    ``HERMOD_PG_TEST_URL`` names a database CI creates for this purpose and the local rig keeps a
    dedicated one for the same reason. Resetting it is the repository contract's own convention.
    """
    value = os.getenv("HERMOD_PG_TEST_URL")
    if not value:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    make_store(value, user_id=USER, agents=(AGENT, OTHER_AGENT))
    return value


@pytest.fixture
def store(url: str) -> RecordProjectRepository:
    return RecordProjectRepository(user_id=USER, db_url=url)


def _service(url: str, *, max_body_bytes: int = 32_000) -> AwakeningService:
    return AwakeningService(
        lambda: RecordProjectRepository(user_id=USER, db_url=url),
        max_body_bytes=max_body_bytes,
    )


def test_an_episode_entity_carries_its_uuid(store) -> None:
    _episode(store, uuid="ep-one", title="one", project="munnin", body=_blocks(3))
    found = store.newest_episode_per_project(agent=AGENT, projects=["munnin"])
    assert [row.uuid for row in found] == ["ep-one"]


def test_the_awakening_pages_each_projects_newest_episode(url: str) -> None:
    store = RecordProjectRepository(user_id=USER, db_url=url)
    _episode(store, uuid="ep-long", title="long", project="munnin", body=_blocks(5))
    _episode(store, uuid="ep-short", title="short", project="other", body=_blocks(2))

    payload = _service(url).awaken(domain=AGENT, projects=["munnin", "other"])

    long_row, short_row = payload["episodes"]
    assert long_row["uuid"] == "ep-long"
    assert (long_row["blocks_returned"], long_row["blocks_total"]) == (3, 5)
    assert long_row["next_cursor"] == "2026-11-10 10.01"
    assert long_row["content"].startswith("### 2026-11-10 10.04")
    assert long_row["content"].count("### 2026-11-10") == 3

    assert (short_row["blocks_returned"], short_row["blocks_total"]) == (2, 2)
    assert short_row["next_cursor"] is None
    # The index is still whole and metadata-only, so a caller can choose without a body.
    assert {entry["uuid"] for entry in payload["episode_index"]} == {"ep-long", "ep-short"}
    assert all("content" not in entry for entry in payload["episode_index"])


def test_the_cursor_names_the_first_group_not_returned(url: str) -> None:
    """The value a caller sends to Munnin's ``get`` must be the next group's own timestamp."""
    store = RecordProjectRepository(user_id=USER, db_url=url)
    body = _blocks(5)
    _episode(store, uuid="ep-long", title="long", project="munnin", body=body)
    row = _service(url).awaken(domain=AGENT, projects=["munnin"])["episodes"][0]

    assert row["next_cursor"] == "2026-11-10 10.01"
    continuation = page_body(body, cursor=row["next_cursor"])
    assert continuation.content.startswith("### 2026-11-10 10.01")
    assert continuation.next_cursor is None
    # Page one plus the continuation is the stored body, byte for byte.
    assert row["content"] + continuation.content == body


def test_an_untimed_episode_is_one_atomic_page(url: str) -> None:
    store = RecordProjectRepository(user_id=USER, db_url=url)
    _episode(store, uuid="ep-plain", title="plain", project="munnin", body="no headings here")
    row = _service(url).awaken(domain=AGENT, projects=["munnin"])["episodes"][0]
    assert (row["blocks_returned"], row["blocks_total"]) == (1, 1)
    assert row["content"] == "no headings here"
    assert row["next_cursor"] is None


def test_the_ceiling_can_shrink_a_page_below_the_block_limit(url: str) -> None:
    store = RecordProjectRepository(user_id=USER, db_url=url)
    _episode(store, uuid="ep-long", title="long", project="munnin", body=_blocks(5))
    row = _service(url, max_body_bytes=60).awaken(domain=AGENT, projects=["munnin"])["episodes"][0]
    assert row["blocks_returned"] == 1
    assert row["blocks_total"] == 5
    assert row["next_cursor"] == "2026-11-10 10.03"


def test_a_project_without_a_live_episode_is_absent(url: str) -> None:
    store = RecordProjectRepository(user_id=USER, db_url=url)
    _episode(store, uuid="ep-live", title="live", project="munnin", body=_blocks(2))
    _episode(
        store, uuid="ep-archived", title="archived", project="other", body=_blocks(2),
        created="2026-11-11T00:00:00+00:00", archived="2026-11-12T00:00:00+00:00",
    )
    payload = _service(url).awaken(domain=AGENT, projects=["munnin", "other", "missing"])
    assert [row["project"] for row in payload["episodes"]] == ["munnin"]


def test_another_agent_gets_no_episode_rows(url: str) -> None:
    store = RecordProjectRepository(user_id=USER, db_url=url)
    _episode(store, uuid="ep-live", title="live", project="munnin", body=_blocks(2))
    payload = _service(url).awaken(domain=OTHER_AGENT, projects=["munnin"])
    assert payload["episodes"] == []
    assert payload["episode_index"] == []


def test_the_page_ceiling_is_refused_when_it_cannot_hold_a_block(url: str) -> None:
    with pytest.raises(ValueError):
        _service(url, max_body_bytes=0)
