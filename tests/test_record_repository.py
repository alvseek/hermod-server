"""The contract for the store Hermod reads: Valaskjalf's record tables.

A project-context entry is a `knowledge` record carrying a `project`, the agent half is
agent-scoped, and the map is a `shared_record` row. It supersedes `test_store_backends.py`, which
covered the store Hermod no longer owns and is deleted with it.

**PostgreSQL only, because PostgreSQL is the only engine deployed.** The store has no local shape —
a verified token names the tenant a call may touch, so with no issuer every store op raises rather
than answering under a guessed account — which means the only store Hermod ever reads is the
deployed one. A SQLite branch would be a second implementation of this contract that nothing runs
and nothing would notice drifting. The suite skips without `HERMOD_PG_TEST_URL`, and CI sets it from
a service container, so the contract runs on every push against the engine that ships.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastmcp import Client

from hermod.api_mcp.server import build_mcp
from hermod.business_services.awakening_service import AwakeningService
from hermod.business_services.context_service import MAX_ENTRY_LINES, ContextService
from hermod.content.loader import ContentLoader, ContentSource
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.record_project_repository import (
    _MEMORY_INSERT,
    _SHARED_INSERT,
    CODING_REASONING_TYPE,
    EPISODE_TYPE,
    MAP_TITLE,
    RECORD_TYPE,
    RecordProjectRepository,
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
    created = store.create_shared_context_entry(
        _entry("build", content_summary="how it is built")
    )
    assert created.uuid is not None
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT record_type, project, title, content_summary FROM shared_record "
            "WHERE uuid = %s",
            (created.uuid,),
        )
        row = cur.fetchone()
    assert row["record_type"] == RECORD_TYPE
    assert row["project"] == PROJECT
    assert row["title"] == "build"
    assert row["content_summary"] == "how it is built"
    assert created.theme == "build"


def test_an_agent_entry_lives_in_the_agent_scoped_table(store) -> None:
    created = store.create_agent_context_entry(
        _entry("notes", scope=ContextScope.agent), agent=AGENT
    )
    assert created.scope is ContextScope.agent
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM memory_record WHERE uuid = %s", (created.uuid,))
        assert cur.fetchone()["agent_id"] == AGENT
    # Another agent's rows are other rows, not a filtered view of this one.
    assert store.list_agent_context(project=PROJECT, agent=OTHER_AGENT) == []


# --- project context -----------------------------------------------------------------


def test_an_entry_round_trips_with_every_field(store) -> None:
    created = store.create_shared_context_entry(
        _entry("build", content_summary="how it is built", tags=["docker", "kamal"])
    )
    assert created.created_date and created.modified_date

    (read,) = store.list_shared_context(project=PROJECT)
    assert read.uuid == created.uuid
    assert read.full_content == created.full_content
    assert read.content_summary == "how it is built"
    assert read.tags == ["docker", "kamal"]


def test_a_create_assigns_a_uuid_and_a_second_one_on_a_theme_is_allowed(store) -> None:
    """No theme check, by decision: a duplicate theme is an allowed state.

    The caller's own listing is what decides between creating and updating, so the store
    neither refuses the second create nor revives a retired row on its behalf.
    """
    first = store.create_shared_context_entry(
        _entry("build", full_content="# build\n\nfirst\n")
    )
    second = store.create_shared_context_entry(
        _entry("build", full_content="# build\n\nsecond\n")
    )
    assert first.uuid != second.uuid
    assert [e.theme for e in store.list_shared_context(project=PROJECT)] == ["build", "build"]


def test_an_agent_create_for_an_unknown_agent_is_refused(store) -> None:
    """The foreign key is the enforcer; this is only the message it produces."""
    with pytest.raises(ValueError, match="no agent"):
        store.create_agent_context_entry(
            _entry("notes", scope=ContextScope.agent), agent="nobody"
        )


def test_an_agent_listing_needs_an_agent_and_both_lists_hide_retired_rows(store) -> None:
    shared = store.create_shared_context_entry(_entry("build"))
    agent_entry = store.create_agent_context_entry(
        _entry("notes", scope=ContextScope.agent), agent=AGENT
    )
    with pytest.raises(ValueError, match="agent-scoped"):
        store.list_agent_context(project=PROJECT, agent="")
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE shared_record SET archived_date = '2026-10-15T00:00:00+00:00' "
            "WHERE uuid = %s",
            (shared.uuid,),
        )
        cur.execute(
            "UPDATE memory_record SET deleted_date = '2026-10-15T00:00:00+00:00' "
            "WHERE uuid = %s",
            (agent_entry.uuid,),
        )
    assert store.list_shared_context(project=PROJECT) == []
    assert store.list_agent_context(project=PROJECT, agent=AGENT) == []


def test_a_map_row_is_not_a_context_entry(store) -> None:
    store.create_map(OrientationMap(project=PROJECT, full_content="# Map\n"))
    assert store.list_shared_context(project=PROJECT) == []


def test_promoting_keeps_the_uuid_and_tombstones_the_source(store) -> None:
    created = store.create_agent_context_entry(
        _entry("build", scope=ContextScope.agent), agent=AGENT
    )
    moved = store.promote_context_entry(uuid=created.uuid)
    assert moved.uuid == created.uuid
    assert moved.scope is ContextScope.shared
    assert moved.full_content == created.full_content
    assert store.list_agent_context(project=PROJECT, agent=AGENT) == []
    (live,) = store.list_shared_context(project=PROJECT)
    assert live.uuid == created.uuid


def test_demoting_needs_an_owner_and_keeps_the_uuid(store) -> None:
    created = store.create_shared_context_entry(_entry("build"))
    moved = store.demote_context_entry(uuid=created.uuid, agent=AGENT)
    assert moved.uuid == created.uuid
    assert moved.scope is ContextScope.agent
    (live,) = store.list_agent_context(project=PROJECT, agent=AGENT)
    assert live.uuid == created.uuid


def test_a_direction_change_into_the_agent_layer_needs_an_existing_owner(store) -> None:
    """The foreign key is the enforcer, and the message is the store's, as it is on a create."""
    created = store.create_shared_context_entry(_entry("notes"))
    with pytest.raises(ValueError, match="agent-scoped"):
        store.demote_context_entry(uuid=created.uuid, agent="")
    with pytest.raises(ValueError, match="no agent"):
        store.demote_context_entry(uuid=created.uuid, agent="nobody")


def test_a_direction_change_refuses_a_row_already_in_the_target_layer(store) -> None:
    created = store.create_shared_context_entry(_entry("build"))
    with pytest.raises(ValueError, match="already lives in"):
        store.promote_context_entry(uuid=created.uuid)


def test_a_direction_change_onto_an_occupied_theme_is_allowed(store) -> None:
    """No theme check, by decision: a second row on one theme is an accepted state.

    The move sub-flow's own collision step decides whether a move is called at all, so the store
    does not refuse one here where a create with the same effect would be allowed.
    """
    agent_side = store.create_agent_context_entry(
        _entry("build", scope=ContextScope.agent), agent=AGENT
    )
    shared_side = store.create_shared_context_entry(_entry("build"))

    moved = store.promote_context_entry(uuid=agent_side.uuid)

    assert moved.scope is ContextScope.shared
    shared = store.list_shared_context(project=PROJECT)
    assert [row.theme for row in shared] == ["build", "build"]
    assert {row.uuid for row in shared} == {shared_side.uuid, agent_side.uuid}


def test_two_live_maps_are_named_rather_than_picked_from(store) -> None:
    """Uniqueness is the uuid's, so a second map row is possible in principle.

    The map is read by project, so serving one of two arbitrarily would answer about a map nobody
    asked for. It is named instead, the same way a uuid held by both layers is.
    """
    store.create_map(OrientationMap(project=PROJECT, full_content="# Map\n\nfirst\n"))
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO shared_record ({', '.join(_SHARED_INSERT)}) "
            f"VALUES ({', '.join(['%s'] * len(_SHARED_INSERT))})",
            (
                "11111111-2222-3333-4444-555555555555", USER, RECORD_TYPE, PROJECT, MAP_TITLE,
                "[]", "2026-01-01", "2026-01-01", None, None, "# Map\n\nsecond\n", None,
            ),
        )
    with pytest.raises(ValueError, match="live orientation maps"):
        store.get_map(project=PROJECT)


def test_a_uuid_held_by_both_layers_raises(store) -> None:
    """`uuid` is unique within a table and not across the two, so a clash is named, not guessed."""
    shared = store.create_shared_context_entry(_entry("build"))
    agent_side = store.create_agent_context_entry(
        _entry("notes", scope=ContextScope.agent), agent=AGENT
    )
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE memory_record SET uuid = %s WHERE uuid = %s",
            (shared.uuid, agent_side.uuid),
        )
    with pytest.raises(ValueError, match="both layers"):
        store.promote_context_entry(uuid=shared.uuid)


# --- the orientation map -------------------------------------------------------------


def test_a_project_starts_with_no_map(store) -> None:
    """The read answers the existence question too: an absent map is ``None``."""
    assert store.get_map(project=PROJECT) is None


def test_the_map_is_created_once_and_updated_after(store) -> None:
    created = store.create_map(
        OrientationMap(project=PROJECT, full_content="# Map\n\nfirst\n", content_summary="the map")
    )
    with store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT uuid, record_type, project FROM shared_record "
            "WHERE title = %s AND project = %s",
            (MAP_TITLE, PROJECT),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, "a project has one map row"
    assert rows[0]["uuid"] is not None, "the row's uuid is assigned at insert"
    assert rows[0]["record_type"] == RECORD_TYPE
    assert rows[0]["project"] == PROJECT
    assert store.get_map(project=PROJECT).full_content == created.full_content

    with pytest.raises(ValueError, match="already has"):
        store.create_map(OrientationMap(project=PROJECT, full_content="# Map\n\nsecond\n"))

    written = store.update_map(
        OrientationMap(
            project=PROJECT, full_content="# Map\n\nsecond\n", content_summary="rewritten"
        )
    )
    assert written.full_content == "# Map\n\nsecond\n"
    assert store.get_map(project=PROJECT).content_summary == "rewritten"


def test_updating_a_map_that_does_not_exist_is_a_lookup_error(store) -> None:
    with pytest.raises(LookupError):
        store.update_map(OrientationMap(project=PROJECT, full_content="# Map\n"))


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


def test_a_create_must_declare_the_stamp_the_served_template_carries(store) -> None:
    loader, service = _service(store)
    stamp = loader.template_version("project-context-template")

    with pytest.raises(ValueError, match="template_version is required"):
        service.create_shared_context_entry(
            project=PROJECT, theme="t", content="# T\n", template_version="",
            content_summary="one line",
        )
    with pytest.raises(ValueError, match="does not match"):
        service.create_shared_context_entry(
            project=PROJECT, theme="t", content="# T\n", template_version="stale",
            content_summary="one line",
        )
    created = service.create_shared_context_entry(
        project=PROJECT, theme="t", content="# T\n", template_version=stamp,
        content_summary="one line",
    )
    assert created.theme == "t"
    assert created.uuid is not None


def test_a_create_must_state_a_summary(store) -> None:
    """The line a listing shows beside the theme is required, and blank is refused."""
    loader, service = _service(store)
    stamp = loader.template_version("project-context-template")

    with pytest.raises(ValueError, match="content_summary is required"):
        service.create_shared_context_entry(
            project=PROJECT, theme="t", content="# T\n", template_version=stamp,
            content_summary="   ",
        )


def test_an_entry_over_the_line_cap_is_refused_rather_than_split(store) -> None:
    """The markdown backend splits an overgrown entry; a row has no second file, so it refuses."""
    _, service = _service(store)
    stamp = service._template_versions["project-context-template"]
    huge = "\n".join(f"line {i}" for i in range(MAX_ENTRY_LINES + 1))

    with pytest.raises(ValueError, match=f"over the {MAX_ENTRY_LINES}-line cap"):
        service.create_shared_context_entry(
            project=PROJECT, theme="big", content=huge, template_version=stamp,
            content_summary="one line",
        )


async def test_reads_need_no_overlay_checkout_and_writes_say_why_they_do(store) -> None:
    """A store's reads are the store's. Its writes need the template, which needs the checkout."""
    loader, service = _service(store, with_overlay=False)
    mcp = build_mcp(loader, service, AwakeningService(lambda: store))

    async with Client(mcp) as client:
        assert await client.list_prompts() == []
        listed = await client.call_tool("list_shared_context", {"project": PROJECT})
        assert listed.data == []
        with pytest.raises(Exception, match="is not served"):
            await client.call_tool(
                "create_shared_context_entry",
                {
                    "project": PROJECT,
                    "theme": "t",
                    "content": "# T\n",
                    "template_version": "anything",
                    "content_summary": "one line",
                },
            )


# --- the memory rows the coding awakening reads ---------------------------------------
#
# Hermod reads Munnin's episodes and its coding lane and never writes either. Nothing here can
# call Munnin, so the parity these tests assert is the *filter* parity: the same lifecycle rules
# Munnin's own reads apply, exercised against rows that would come back if either filter went
# missing.


def _row(
    store: RecordProjectRepository,
    *,
    table: str,
    record_type: str,
    title: str,
    project: str | None = None,
    agent: str = AGENT,
    created: str = "2026-01-01T00:00:00+00:00",
    archived: str | None = None,
    deleted: str | None = None,
    summary: str | None = None,
) -> None:
    """Seed one record the way Munnin would have written it.

    The repository has no writer for these types by design (ADR-029), so a test writes the row
    the store's own shape carries rather than asking Hermod to invent one.
    """
    columns = [
        "uuid", "user_id", "record_type", "project", "title", "tags",
        "created_date", "modified_date", "archived_date", "deleted_date",
        "full_content", "content_summary",
    ]
    values: list[object] = [
        f"uuid-{table}-{title}", USER, record_type, project, title, "[]",
        created, created, archived, deleted, f"# {title}\n\nbody of {title}\n", summary,
    ]
    if table == "memory_record":
        columns.insert(2, "agent_id")
        values.insert(2, agent)
    with store._conn() as conn, conn.cursor() as cur:  # noqa: SLF001 — the fixture is the seam
        cur.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})",
            values,
        )


def test_the_newest_live_episode_is_read_per_project(store) -> None:
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="older", project="one",
         created="2026-01-01T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="newer", project="one",
         created="2026-01-02T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="other", project="two",
         created="2026-01-01T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="outside", project="three",
         created="2026-01-09T00:00:00+00:00")

    found = store.newest_episode_per_project(agent=AGENT, projects=["one", "two", "four"])

    assert [e.title for e in found] == ["newer", "other"]
    assert [e.project for e in found] == ["one", "two"]
    assert found[0].full_content.startswith("# newer")


def test_an_archived_or_tombstoned_episode_is_not_carried_forward(store) -> None:
    """The filter parity, asserted where it can fail.

    Both retired rows are *newer* than the live one, so a missing predicate does not return a
    wrong order, it returns the wrong episode. Deleting either filter from the query turns this
    test red, which is the only thing that keeps two servers agreeing about what "live" means.
    """
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="live", project=PROJECT,
         created="2026-01-01T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="archived", project=PROJECT,
         created="2026-01-02T00:00:00+00:00", archived="2026-01-03T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="deleted", project=PROJECT,
         created="2026-01-04T00:00:00+00:00", deleted="2026-01-05T00:00:00+00:00")

    found = store.newest_episode_per_project(agent=AGENT, projects=[PROJECT])

    assert [e.title for e in found] == ["live"]


def test_an_episode_of_another_agent_is_not_read(store) -> None:
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="mine", project=PROJECT,
         agent=AGENT)
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="theirs", project=PROJECT,
         agent=OTHER_AGENT, created="2026-02-01T00:00:00+00:00")

    found = store.newest_episode_per_project(agent=AGENT, projects=[PROJECT])

    assert [e.title for e in found] == ["mine"]


def test_no_projects_asks_nothing_and_gets_nothing(store) -> None:
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="live", project=PROJECT)
    assert store.newest_episode_per_project(agent=AGENT, projects=[]) == []


def test_the_episode_index_spans_the_set_and_carries_no_bodies(store) -> None:
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="one older", project="one",
         created="2026-01-01T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="one newer", project="one",
         created="2026-01-02T00:00:00+00:00", summary="what the newer session did")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="two", project="two",
         created="2026-01-03T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="outside", project="three",
         created="2026-01-04T00:00:00+00:00")
    _row(store, table="memory_record", record_type=EPISODE_TYPE, title="retired", project="one",
         created="2026-01-05T00:00:00+00:00", archived="2026-01-06T00:00:00+00:00")

    index = store.episode_index(agent=AGENT, projects=["one", "two"])

    assert [e.title for e in index] == ["two", "one newer", "one older"]
    assert [e.project for e in index] == ["two", "one", "one"]
    # the one-liner rides the index, so a reader can choose an episode without a `get`
    assert [e.content_summary for e in index] == [None, "what the newer session did", None]
    # the projection is the point: a body is what this read exists to avoid carrying
    assert not hasattr(index[0], "full_content")
    assert store.episode_index(agent=AGENT, projects=[]) == []


def test_the_coding_lane_is_read_whole_and_retired_rows_are_not(store) -> None:
    _row(store, table="shared_record", record_type=CODING_REASONING_TYPE, title="B pattern")
    _row(store, table="shared_record", record_type=CODING_REASONING_TYPE, title="A pattern")
    _row(store, table="shared_record", record_type=CODING_REASONING_TYPE, title="archived",
         archived="2026-01-02T00:00:00+00:00")
    _row(store, table="shared_record", record_type=CODING_REASONING_TYPE, title="deleted",
         deleted="2026-01-02T00:00:00+00:00")
    # Neither a project-context entry nor the map is the lane.
    _row(store, table="shared_record", record_type=RECORD_TYPE, title="entry", project=PROJECT)

    lane = store.coding_lane()

    assert [p.title for p in lane] == ["A pattern", "B pattern"]
    assert lane[0].full_content.startswith("# A pattern")


# --- the payload's set, across both servers' rows -------------------------------------


def test_the_payload_spans_the_set_for_context_and_maps(store) -> None:
    """`projects[0]` is the session's *home*, and never a filter on what comes back.

    The tool's docstring and the service's both used to say the context index and the maps came
    from the first element alone, which tells a caller to fetch a related repository's index
    separately and defeats the one call the composite exists for. Seeding two repositories and
    asserting both is what keeps the prose and the loop from drifting apart again: drop either
    loop in ``AwakeningService.awaken`` and the second repository's rows disappear here.
    Each context row also carries its `uuid`, which is what makes the index actionable: that value
    is how the memory layer's own read, edit and retire ops are addressed.
    """
    home = store.create_shared_context_entry(
        ContextEntry(project="home", scope=ContextScope.shared, theme="h", full_content="# h\n")
    )
    sub = store.create_shared_context_entry(
        ContextEntry(project="sub", scope=ContextScope.shared, theme="s", full_content="# s\n")
    )
    store.create_map(OrientationMap(project="home", full_content="# Map\n\nhome\n"))

    payload = AwakeningService(lambda: store).awaken(domain=AGENT, projects=["home", "sub"])

    assert [
        (row["project"], row["theme"], row["uuid"]) for row in payload["context_index"]
    ] == [("home", "h", home.uuid), ("sub", "s", sub.uuid)]
    assert [row["project"] for row in payload["maps"]] == ["home"]


# --- the schema copy, pinned to the code rather than trusted -------------------------

#: Columns the code names outside its insert lists: in a `WHERE`, a `SET`, or when mapping a row.
#: `archived_date` is named because both the episode reads and the context reads filter it: the
#: memory layer archives rows, and this store retires a row when the store it reads does.
_READ_BY_NAME = (
    "id",
    "user_id",
    "record_type",
    "project",
    "title",
    "tags",
    "created_date",
    "modified_date",
    "archived_date",
    "deleted_date",
    "full_content",
    "content_summary",
)


def _columns_of(store: RecordProjectRepository, table: str) -> set[str]:
    with store._conn() as conn, conn.cursor() as cur:  # noqa: SLF001 — the fixture is the seam
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
        )
        return {row["column_name"] for row in cur.fetchall()}


@pytest.mark.parametrize(
    ("table", "insert"),
    [
        ("shared_record", _SHARED_INSERT),
        ("memory_record", _MEMORY_INSERT),
    ],
)
def test_the_schema_copy_carries_every_column_the_code_names(
    store: RecordProjectRepository, table: str, insert: tuple[str, ...]
) -> None:
    """`tests/valaskjalf_schema.py` is a *copy* of Munnin's DDL, so it is pinned to the code.

    The suite runs against that copy, which means a column it lacks fails every op — but only the
    ones a test happens to exercise. This asks the question directly instead: every column the code
    inserts into, filters on or maps out of must exist in the table it names. A rename landing in
    the code and not in the copy therefore fails here rather than in a deployment.

    The other direction — the copy carrying a column Munnin's real schema does not have — is not
    checkable from this repo, because Munnin's DDL lives in `agent-memory-server`. The copy is
    allowed to be wider than what this server uses; it must not be narrower.
    """
    present = _columns_of(store, table)
    named = set(insert) | set(_READ_BY_NAME) | ({"agent_id"} if table == "memory_record" else set())
    missing = named - present
    assert not missing, f"{table} lacks {sorted(missing)}, which the code names"