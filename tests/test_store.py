"""The project store: schema, migrations and the repository contract.

Two things are pinned here. The migration runner reaches a fresh database in one call and
changes nothing on a re-run — the property ``CREATE TABLE IF NOT EXISTS`` alone cannot
give. And the repository round-trips both entities, with the three behaviours the served
ops depend on: a move that changes only the scope, a delete that removes the entry from
every read, and a create that refuses to overwrite.

The store is a temp file per test: the repository is connection-per-operation, so a
``:memory:`` path would not persist between calls.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from fastmcp import Client

from hermod.api_mcp.server import _scope_arg, build_mcp
from hermod.business_services.context_service import MAX_ENTRY_LINES, ContextService, SizeVerdict
from hermod.content.loader import ContentLoader, ContentSource
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_entities.schema_migrations import apply_migrations, known, status
from hermod.data_repositories.sqlite_project_repository import SqliteProjectRepository


@pytest.fixture
def repo(tmp_path):
    return SqliteProjectRepository(tmp_path / "hermod.db")


def _entry(theme: str = "hermod-server-build", *, scope=ContextScope.shared, body="# Entry\n"):
    return ContextEntry(
        project="agent-memory-project",
        scope=scope,
        theme=theme,
        full_content=body,
        description="what this covers",
        tags=["alpha", "beta"],
    )


# --- migrations ---


def test_migrations_apply_to_a_fresh_database(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "fresh.db")
    try:
        assert [state for _, state in status(conn)] == ["pending"] * len(known())
        ran = apply_migrations(conn)
        assert ran == [m.name for m in known()]
        assert [state for _, state in status(conn)] == ["applied"] * len(known())
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"project_context", "orientation_map", "migration_history"} <= tables
    finally:
        conn.close()


def test_migrations_are_idempotent_on_re_run(tmp_path) -> None:
    path = tmp_path / "once.db"
    conn = sqlite3.connect(path)
    try:
        first = apply_migrations(conn)
        second = apply_migrations(conn)
    finally:
        conn.close()
    assert first  # the fresh database ran every migration
    assert second == []  # a re-run changes nothing


def test_a_migration_edited_after_it_ran_is_refused(tmp_path, monkeypatch) -> None:
    """A changed file is refused rather than silently skipped — the reason the checksum
    is stored at all."""
    path = tmp_path / "edited.db"
    conn = sqlite3.connect(path)
    try:
        apply_migrations(conn)
        original = known()[0]
        edited = original.__class__(
            version=original.version,
            name=original.name,
            sql=original.sql + "\n-- an edit",
            checksum=hashlib.sha256((original.sql + "\n-- an edit").encode()).hexdigest(),
        )
        monkeypatch.setattr(
            "hermod.data_entities.schema_migrations.known", lambda: [edited]
        )
        with pytest.raises(RuntimeError, match="changed after it was applied"):
            apply_migrations(conn)
    finally:
        conn.close()


# --- context entries ---


def test_context_entry_round_trips(repo) -> None:
    created = repo.create_context_entry(_entry())
    assert created.id is not None
    assert created.scope is ContextScope.shared
    assert created.tags == ["alpha", "beta"]
    assert created.created_date and created.modified_date

    fetched = repo.get_context_entry(
        project="agent-memory-project", scope=ContextScope.shared, theme="hermod-server-build"
    )
    assert fetched is not None
    assert fetched.full_content == "# Entry\n"
    assert fetched.description == "what this covers"
    assert repo.list_context_entries(project="agent-memory-project") == [fetched]


def test_a_fresh_store_reaches_the_schema_in_one_call(repo) -> None:
    """No migration step is run by the test: the repository's first operation applies the
    schema, which is what makes a fresh database usable in one call."""
    assert repo.list_context_entries(project="nothing-yet") == []
    assert repo.map_exists(project="nothing-yet") is False


def test_create_refuses_to_overwrite_an_existing_theme(repo) -> None:
    repo.create_context_entry(_entry())
    with pytest.raises(ValueError, match="already exists under theme"):
        repo.create_context_entry(_entry(body="# A different body\n"))
    # The original is intact — a refusal writes nothing.
    kept = repo.get_context_entry(
        project="agent-memory-project", scope=ContextScope.shared, theme="hermod-server-build"
    )
    assert kept is not None and kept.full_content == "# Entry\n"


def test_update_replaces_the_body_and_requires_a_live_entry(repo) -> None:
    repo.create_context_entry(_entry())
    updated = repo.update_context_entry(_entry(body="# Rewritten\n"))
    assert updated.full_content == "# Rewritten\n"
    with pytest.raises(LookupError, match="no live shared context entry"):
        repo.update_context_entry(_entry(theme="never-created"))


def test_move_changes_only_the_scope(repo) -> None:
    repo.create_context_entry(_entry(scope=ContextScope.private, body="# Body\n"))
    moved = repo.move_context_entry(
        project="agent-memory-project",
        theme="hermod-server-build",
        from_scope=ContextScope.private,
        to_scope=ContextScope.shared,
    )
    assert moved.scope is ContextScope.shared
    assert moved.full_content == "# Body\n"  # untouched
    assert (
        repo.get_context_entry(
            project="agent-memory-project",
            scope=ContextScope.private,
            theme="hermod-server-build",
        )
        is None
    )


def test_a_move_into_an_occupied_scope_is_refused(repo) -> None:
    repo.create_context_entry(_entry(scope=ContextScope.private))
    repo.create_context_entry(_entry(scope=ContextScope.shared))
    with pytest.raises(ValueError, match="already holds that theme"):
        repo.move_context_entry(
            project="agent-memory-project",
            theme="hermod-server-build",
            from_scope=ContextScope.private,
            to_scope=ContextScope.shared,
        )


def test_delete_tombstones_and_frees_the_theme(repo) -> None:
    repo.create_context_entry(_entry())
    repo.delete_context_entry(
        project="agent-memory-project", scope=ContextScope.shared, theme="hermod-server-build"
    )
    assert repo.list_context_entries(project="agent-memory-project") == []
    assert (
        repo.get_context_entry(
            project="agent-memory-project",
            scope=ContextScope.shared,
            theme="hermod-server-build",
        )
        is None
    )
    # A tombstone is not a live entry, so the same theme can be created again.
    recreated = repo.create_context_entry(_entry(body="# New life\n"))
    assert recreated.full_content == "# New life\n"
    with pytest.raises(LookupError, match="no private context entry"):
        repo.delete_context_entry(
            project="agent-memory-project", scope=ContextScope.private, theme="never-there"
        )


# --- the orientation map ---


def test_map_round_trips(repo) -> None:
    assert repo.map_exists(project="agent-memory-project") is False
    assert repo.get_map(project="agent-memory-project") is None

    created = repo.create_map(
        OrientationMap(project="agent-memory-project", full_content="# Map\n")
    )
    assert created.created_date and created.modified_date
    assert repo.map_exists(project="agent-memory-project") is True

    written = repo.write_map(
        OrientationMap(project="agent-memory-project", full_content="# Map v2\n")
    )
    assert written.full_content == "# Map v2\n"
    assert written.created_date == created.created_date  # create date survives a write

    with pytest.raises(ValueError, match="already has an orientation map"):
        repo.create_map(OrientationMap(project="agent-memory-project", full_content="# Third\n"))
    with pytest.raises(LookupError, match="has no orientation map to write"):
        repo.write_map(OrientationMap(project="other-project", full_content="# Nope\n"))


# --- the service layer: one test per op the served db.md names ---


@pytest.fixture
def service(repo) -> ContextService:
    return ContextService(repo)


def test_service_create_list_and_read(service) -> None:
    service.create_context_entry(
        project="agent-memory-project",
        scope=ContextScope.private,
        theme="deployment",
        content="# Deployment\n",
        description="how it ships",
        tags=["deploy"],
    )
    listed = service.list_context_entries(project="agent-memory-project")
    assert [(e.scope.value, e.theme) for e in listed] == [("private", "deployment")]
    assert listed[0].description == "how it ships"
    assert (
        service.list_context_entries(project="agent-memory-project", scope=ContextScope.shared)
        == []
    )

    read = service.read_context_entry(
        project="agent-memory-project", scope=ContextScope.private, theme="deployment"
    )
    assert read is not None and read.full_content == "# Deployment\n"
    assert (
        service.read_context_entry(
            project="agent-memory-project", scope=ContextScope.shared, theme="deployment"
        )
        is None
    )


def test_service_update_and_move(service) -> None:
    service.create_context_entry(
        project="p", scope=ContextScope.private, theme="t", content="one\n"
    )
    service.update_context_entry(
        project="p", scope=ContextScope.private, theme="t", content="two\n"
    )
    moved = service.move_context_entry(
        project="p", theme="t", from_scope=ContextScope.private, to_scope=ContextScope.shared
    )
    assert moved.scope is ContextScope.shared
    assert moved.full_content == "two\n"  # the move touched the scope, not the body


def test_service_delete(service) -> None:
    service.create_context_entry(project="p", scope=ContextScope.shared, theme="t", content="x\n")
    service.delete_context_entry(project="p", scope=ContextScope.shared, theme="t")
    assert service.list_context_entries(project="p") == []


def test_check_entry_size_measures_and_create_refuses(service) -> None:
    assert service.check_entry_size(content="a\nb\n") == SizeVerdict(
        lines=2, max_lines=MAX_ENTRY_LINES, oversized=False
    )
    huge = "\n".join("x" for _ in range(MAX_ENTRY_LINES + 1))
    assert service.check_entry_size(content=huge).oversized is True

    with pytest.raises(ValueError, match="over the"):
        service.create_context_entry(
            project="p", scope=ContextScope.shared, theme="huge", content=huge
        )
    with pytest.raises(ValueError, match="over the"):
        service.update_context_entry(
            project="p", scope=ContextScope.shared, theme="huge", content=huge
        )


def test_service_map_ops(service) -> None:
    assert service.map_exists(project="p") is False
    assert service.read_map(project="p") is None
    service.create_map(project="p", content="# Map\n")
    assert service.map_exists(project="p") is True
    written = service.write_map(project="p", content="# Map 2\n")
    assert written.full_content == "# Map 2\n"
    assert service.read_map(project="p").full_content == "# Map 2\n"


def test_an_unknown_scope_is_named() -> None:
    assert _scope_arg(None) is None
    assert _scope_arg("private") is ContextScope.private
    with pytest.raises(ValueError, match="expected 'shared' or 'private'"):
        _scope_arg("public")


# --- the same ops through the MCP face ---


async def test_every_store_tool_round_trips_without_an_overlay(tmp_path) -> None:
    """The store is a local file, so it is served whether or not an overlay checkout is present.

    Every op ``db.md`` names is exercised here through the tool the served mechanic will call, so a
    missing registration fails the test by raising rather than by a name drifting out of a list.
    """
    loader = ContentLoader([ContentSource(name="absent", root=tmp_path / "no-checkout")])
    service = ContextService(SqliteProjectRepository(tmp_path / "hermod.db"))
    mcp = build_mcp(loader, service)

    async with Client(mcp) as client:
        assert await client.list_prompts() == []  # no checkout, so nothing but the store

        created = (
            await client.call_tool(
                "create_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "shared",
                    "theme": "hermod-server-build",
                    "content": "# Entry\n",
                    "description": "what it covers",
                    "tags": ["hermod-server"],
                },
            )
        ).data
        assert created["scope"] == "shared" and created["theme"] == "hermod-server-build"

        listing = (
            await client.call_tool("list_context_entries", {"project": "agent-memory-project"})
        ).data
        assert [row["theme"] for row in listing] == ["hermod-server-build"]
        assert "content" not in listing[0]  # the index projection is metadata only

        read = (
            await client.call_tool(
                "read_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "shared",
                    "theme": "hermod-server-build",
                },
            )
        ).data
        assert read["found"] is True and read["content"] == "# Entry\n"

        updated = (
            await client.call_tool(
                "update_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "shared",
                    "theme": "hermod-server-build",
                    "content": "# Entry, revised\n",
                },
            )
        ).data
        assert updated["theme"] == "hermod-server-build"

        moved = (
            await client.call_tool(
                "move_context_entry",
                {
                    "project": "agent-memory-project",
                    "theme": "hermod-server-build",
                    "from_scope": "shared",
                    "to_scope": "private",
                },
            )
        ).data
        assert moved["scope"] == "private"

        size = (await client.call_tool("check_entry_size", {"content": "a\nb\n"})).data
        assert size == {"lines": 2, "max_lines": MAX_ENTRY_LINES, "oversized": False}

        assert (
            await client.call_tool("map_exists", {"project": "agent-memory-project"})
        ).data["exists"] is False
        await client.call_tool(
            "create_map", {"project": "agent-memory-project", "content": "# Map\n"}
        )
        await client.call_tool(
            "write_map", {"project": "agent-memory-project", "content": "# Map, revised\n"}
        )
        served_map = (
            await client.call_tool("read_map", {"project": "agent-memory-project"})
        ).data
        assert served_map["found"] is True and served_map["content"] == "# Map, revised\n"

        assert (
            await client.call_tool(
                "delete_context_entry",
                {
                    "project": "agent-memory-project",
                    "scope": "private",
                    "theme": "hermod-server-build",
                },
            )
        ).data["deleted"] is True
        assert (
            await client.call_tool("list_context_entries", {"project": "agent-memory-project"})
        ).data == []
