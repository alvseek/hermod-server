"""The contract for the store Hermod reads: Valaskjalf's record tables.

A project-context entry is a `knowledge` record carrying a `project`, the private half is
agent-scoped, and the map is a `shared_record` row. It supersedes `test_store_backends.py`, which
covered the store Hermod no longer owns and is deleted with it.

**PostgreSQL only, because PostgreSQL is the only engine deployed.** Hermod has no local serving
shape, so a SQLite branch would be a second implementation of this contract that nothing runs and
nothing would notice drifting. The suite skips without `HERMOD_PG_TEST_URL`, and CI sets it from a
service container, so the contract runs on every push against the engine that ships.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastmcp import Client

from hermod.api_mcp.server import build_mcp
from hermod.business_services.context_service import MAX_ENTRY_LINES, ContextService
from hermod.content.loader import ContentLoader, ContentSource
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.record_project_repository import (
    MAP_TITLE,
    RECORD_TYPE,
    RecordProjectRepository,
    entry_uuid,
    map_uuid,
)
from tests.valaskjalf_schema import make_store

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"

USER = "alvi"
PROJECT = "hermod-server"
AGENT = "meta"
OTHER_AGENT = "invintiry"

pytestmark = pytest.mark.postgres


@pytest.fixture
def store(tmp_path: Path) -> RecordProjectRepository:
    url = os.getenv("HERMOD_PG_TEST_URL")
    if not url:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    make_store(url, user_id=USER, agents=(AGENT, OTHER_AGENT))
    return RecordProjectRepository(user_id=USER, db_url=url)


def _entry(theme: str, *, scope: ContextScope = ContextScope.shared, **fields) -> ContextEntry:
    return ContextEntry(
        project=PROJECT,
        scope=scope,
        theme=theme,
        full_content=fields.pop("full_content", f"# {theme}\n\nbody of {theme}\n"),
        **fields,
    )


# --- what the store actually is ------------------------------------------------------


def test_a_shared_entry_is_a_knowledge_record_carrying_the_project(store) -> None:
    """The shape ADR-027 turns on, asserted against the table rather than the interface."""
    created = store.create_context_entry(_entry("build", description="how it is built"))
    identity = entry_uuid(user_id=USER, project=PROJECT, scope=ContextScope.shared, theme="build")
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT record_type, project, title, content_summary FROM shared_record "
            "WHERE uuid = %s",
            (identity,),
        )
        row = cur.fetchone()
    assert row["record_type"] == RECORD_TYPE
    assert row["project"] == PROJECT
    assert row["title"] == "build"
    assert row["content_summary"] == "how it is built"
    assert created.theme == "build"


def test_a_private_entry_lives_in_the_agent_scoped_table(store) -> None:
    store.create_context_entry(_entry("notes", scope=ContextScope.private), agent=AGENT)
    read = store.get_context_entry(
        project=PROJECT, scope=ContextScope.private, theme="notes", agent=AGENT
    )
    assert read is not None
    assert read.scope is ContextScope.private
    # Another agent's private scope is a different row, not a filtered view of this one.
    assert (
        store.get_context_entry(
            project=PROJECT, scope=ContextScope.private, theme="notes", agent=OTHER_AGENT
        )
        is None
    )


# --- project context -----------------------------------------------------------------


def test_an_entry_round_trips_with_every_field(store) -> None:
    created = store.create_context_entry(
        _entry("build", description="how it is built", tags=["docker", "kamal"])
    )
    assert created.created_date and created.modified_date

    read = store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
    assert read is not None
    assert read.full_content == created.full_content
    assert read.description == "how it is built"
    assert read.tags == ["docker", "kamal"]


def test_the_identity_is_derived_so_it_is_stable(store) -> None:
    created = store.create_context_entry(_entry("build"))
    assert created.id is not None
    read = store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
    assert read is not None and read.id == created.id


def test_creating_over_a_live_theme_is_refused(store) -> None:
    store.create_context_entry(_entry("build"))
    with pytest.raises(ValueError, match="already exists"):
        store.create_context_entry(_entry("build"))


def test_a_private_create_for_an_unknown_agent_is_refused(store) -> None:
    """The foreign key is the enforcer; this is only the message it produces."""
    with pytest.raises(ValueError, match="no agent"):
        store.create_context_entry(_entry("notes", scope=ContextScope.private), agent="nobody")


def test_listing_needs_an_agent_once_the_private_scope_is_involved(store) -> None:
    store.create_context_entry(_entry("build"))
    store.create_context_entry(_entry("notes", scope=ContextScope.private), agent=AGENT)

    shared = store.list_context_entries(project=PROJECT, scope=ContextScope.shared)
    assert [e.theme for e in shared] == ["build"]
    with pytest.raises(ValueError, match="agent-scoped"):
        store.list_context_entries(project=PROJECT)
    both = store.list_context_entries(project=PROJECT, agent=AGENT)
    assert [e.theme for e in both] == ["build", "notes"]


def test_a_map_row_is_not_a_context_entry(store) -> None:
    store.create_map(OrientationMap(project=PROJECT, full_content="# Map\n"))
    assert store.list_context_entries(project=PROJECT, scope=ContextScope.shared) == []


def test_update_replaces_body_summary_and_tags(store) -> None:
    created = store.create_context_entry(_entry("build"))
    updated = store.update_context_entry(
        ContextEntry(
            project=PROJECT,
            scope=ContextScope.shared,
            theme="build",
            full_content="# build\n\nrewritten\n",
            description="new summary",
            tags=["x"],
        )
    )
    assert updated.created_date == created.created_date
    assert updated.full_content == "# build\n\nrewritten\n"
    assert updated.description == "new summary"
    assert updated.tags == ["x"]


def test_update_of_an_absent_entry_is_a_lookup_error(store) -> None:
    with pytest.raises(LookupError):
        store.update_context_entry(_entry("absent"))


def test_move_moves_the_row_between_tables(store) -> None:
    created = store.create_context_entry(_entry("build", scope=ContextScope.private), agent=AGENT)
    moved = store.move_context_entry(
        project=PROJECT,
        theme="build",
        from_scope=ContextScope.private,
        to_scope=ContextScope.shared,
        agent=AGENT,
    )
    assert moved.scope is ContextScope.shared
    assert moved.full_content == created.full_content
    assert (
        store.get_context_entry(
            project=PROJECT, scope=ContextScope.private, theme="build", agent=AGENT
        )
        is None
    )
    assert store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")


def test_move_onto_an_occupied_scope_is_refused(store) -> None:
    store.create_context_entry(_entry("build", scope=ContextScope.private), agent=AGENT)
    store.create_context_entry(_entry("build", scope=ContextScope.shared))
    with pytest.raises(ValueError, match="already holds"):
        store.move_context_entry(
            project=PROJECT,
            theme="build",
            from_scope=ContextScope.private,
            to_scope=ContextScope.shared,
            agent=AGENT,
        )


def test_delete_tombstones_and_recreating_revives_the_same_row(store) -> None:
    """The behaviour change derived keys bring, asserted rather than assumed.

    The old store's identity was a partial unique index, so deleting a theme and creating it again
    produced a *new* row. Here the identity is the uuid, so the same theme is the same row, and a
    create over a tombstone revives it: one theme keeps one history.
    """
    first = store.create_context_entry(_entry("build"))
    store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")

    assert (
        store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
        is None
    )
    assert store.list_context_entries(project=PROJECT, scope=ContextScope.shared) == []

    revived = store.create_context_entry(_entry("build", full_content="# build\n\nsecond\n"))
    assert revived.id == first.id
    assert revived.deleted_date is None
    assert "second" in revived.full_content


def test_delete_is_idempotent_but_a_never_present_theme_is_a_lookup_error(store) -> None:
    store.create_context_entry(_entry("build"))
    store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
    store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
    with pytest.raises(LookupError):
        store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="never")


# --- the orientation map -------------------------------------------------------------


def test_a_project_starts_with_no_map(store) -> None:
    assert store.map_exists(project=PROJECT) is False
    assert store.get_map(project=PROJECT) is None


def test_the_map_is_created_once_and_rewritten_after(store) -> None:
    created = store.create_map(
        OrientationMap(project=PROJECT, full_content="# Map\n\nfirst\n", description="the map")
    )
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT title, record_type, project FROM shared_record WHERE uuid = %s",
            (map_uuid(user_id=USER, project=PROJECT),),
        )
        row = cur.fetchone()
    assert row["title"] == MAP_TITLE
    assert row["record_type"] == RECORD_TYPE
    assert row["project"] == PROJECT
    assert store.get_map(project=PROJECT).full_content == created.full_content

    with pytest.raises(ValueError, match="already has"):
        store.create_map(OrientationMap(project=PROJECT, full_content="# Map\n\nsecond\n"))

    written = store.write_map(
        OrientationMap(project=PROJECT, full_content="# Map\n\nsecond\n", description="rewritten")
    )
    assert written.full_content == "# Map\n\nsecond\n"
    assert store.get_map(project=PROJECT).description == "rewritten"


def test_writing_a_map_that_does_not_exist_is_a_lookup_error(store) -> None:
    with pytest.raises(LookupError):
        store.write_map(OrientationMap(project=PROJECT, full_content="# Map\n"))


# --- the service layer, over the same store ------------------------------------------
#
# These three came from `test_store.py` as it went. Its repository and migration tests covered the
# store this plan removes; what had to survive is the *service's* part — the stamp gate, the size
# cap, and the fact that a store's reads need no overlay checkout — because none of that is the
# table's business and none of it is covered anywhere else.


def _service(store, *, with_overlay: bool = True):
    root = CONTENT if with_overlay else Path("no-such-checkout")
    loader = ContentLoader([ContentSource(name="overlay", root=root)])
    versions = {name: loader.template_version(name) for name in loader.list_resources()}
    return loader, ContextService(lambda: store, versions)


def test_a_write_must_declare_the_stamp_the_served_template_carries(store) -> None:
    loader, service = _service(store)
    stamp = loader.template_version("project-context-template")

    with pytest.raises(ValueError, match="template_version is required"):
        service.create_context_entry(
            project=PROJECT, scope=ContextScope.shared, theme="t", content="# T\n",
            template_version="",
        )
    with pytest.raises(ValueError, match="does not match"):
        service.create_context_entry(
            project=PROJECT, scope=ContextScope.shared, theme="t", content="# T\n",
            template_version="stale",
        )
    created = service.create_context_entry(
        project=PROJECT, scope=ContextScope.shared, theme="t", content="# T\n",
        template_version=stamp,
    )
    assert created.theme == "t"


def test_an_entry_over_the_line_cap_is_refused_rather_than_split(store) -> None:
    """The markdown backend splits an overgrown entry; a row has no second file, so it refuses."""
    _, service = _service(store)
    stamp = service._template_versions["project-context-template"]
    huge = "\n".join(f"line {i}" for i in range(MAX_ENTRY_LINES + 1))

    verdict = service.check_entry_size(content=huge)
    assert verdict.oversized is True
    assert verdict.max_lines == MAX_ENTRY_LINES
    with pytest.raises(ValueError, match="over the"):
        service.create_context_entry(
            project=PROJECT, scope=ContextScope.shared, theme="big", content=huge,
            template_version=stamp,
        )


async def test_reads_need_no_overlay_checkout_and_writes_say_why_they_do(store) -> None:
    """A store's reads are the store's. Its writes need the template, which needs the checkout."""
    loader, service = _service(store, with_overlay=False)
    mcp = build_mcp(loader, service)

    async with Client(mcp) as client:
        assert await client.list_prompts() == []
        listed = await client.call_tool(
            "list_context_entries", {"project": PROJECT, "scope": "shared"}
        )
        assert listed.data == []
        with pytest.raises(Exception, match="is not served"):
            await client.call_tool(
                "create_context_entry",
                {
                    "project": PROJECT,
                    "scope": "shared",
                    "theme": "t",
                    "content": "# T\n",
                    "template_version": "anything",
                },
            )
