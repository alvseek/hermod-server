"""One contract, two engines.

Hermod's store ships twice: SQLite for local runs and tests, Postgres for the deployment. The
two have to agree on behaviour, and the only honest way to know they do is to run the same
assertions against both — which is what Munnin's ``memory_repo`` fixture does for its own store,
and why this file mirrors it.

The ``postgres`` parameter is marked and skips unless ``HERMOD_PG_TEST_URL`` is set, so the
default suite needs no service. It is not optional coverage though: a backend with no test
against a real server is a backend that has never run, and the SQLite half would happily pass
while the Postgres half was broken.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.postgres_project_repository import PostgresProjectRepository
from hermod.data_repositories.sqlite_project_repository import SqliteProjectRepository

PROJECT = "hermod-server"


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def store(request: pytest.FixtureRequest, tmp_path: Path):
    """The ``ProjectRepository`` contract, against whichever engine is asked for."""
    if request.param == "sqlite":
        return SqliteProjectRepository(tmp_path / "hermod.db")

    url = os.getenv("HERMOD_PG_TEST_URL")
    if not url:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    import psycopg

    # A fresh schema per run, so a leftover row cannot make an assertion pass.
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    return PostgresProjectRepository(url)


def _entry(theme: str, *, scope: ContextScope = ContextScope.shared, **fields) -> ContextEntry:
    return ContextEntry(
        project=PROJECT,
        scope=scope,
        theme=theme,
        full_content=fields.pop("full_content", f"# {theme}\n\nbody of {theme}\n"),
        **fields,
    )


# --- project context ---------------------------------------------------------------


def test_an_entry_round_trips_with_every_field(store) -> None:
    created = store.create_context_entry(
        _entry("build", description="how it is built", tags=["docker", "kamal"])
    )
    assert created.id is not None
    assert created.created_date and created.modified_date

    read = store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
    assert read is not None
    assert read.full_content == created.full_content
    assert read.description == "how it is built"
    assert read.tags == ["docker", "kamal"]
    assert read.deleted_date is None


def test_creating_over_a_live_theme_is_refused(store) -> None:
    store.create_context_entry(_entry("build"))
    with pytest.raises(ValueError, match="already"):
        store.create_context_entry(_entry("build"))


def test_reading_an_absent_entry_answers_none(store) -> None:
    assert store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="nope") is None


def test_listing_covers_both_scopes_and_narrows_on_request(store) -> None:
    store.create_context_entry(_entry("build", scope=ContextScope.shared))
    store.create_context_entry(_entry("notes", scope=ContextScope.private))

    both = store.list_context_entries(project=PROJECT)
    assert {e.theme for e in both} == {"build", "notes"}

    only_private = store.list_context_entries(project=PROJECT, scope=ContextScope.private)
    assert [e.theme for e in only_private] == ["notes"]

    assert store.list_context_entries(project="another-project") == []


def test_update_replaces_the_body_and_keeps_created_date(store) -> None:
    created = store.create_context_entry(_entry("build"))
    updated = store.update_context_entry(
        ContextEntry(
            project=PROJECT,
            scope=ContextScope.shared,
            theme="build",
            full_content="# build\n\nrewritten\n",
            description="new description",
            tags=["x"],
        )
    )
    assert updated.created_date == created.created_date
    assert updated.full_content == "# build\n\nrewritten\n"
    assert updated.description == "new description"
    assert updated.tags == ["x"]


def test_update_of_an_absent_entry_is_a_lookup_error(store) -> None:
    with pytest.raises(LookupError):
        store.update_context_entry(_entry("absent"))


def test_move_changes_scope_and_leaves_the_body_alone(store) -> None:
    created = store.create_context_entry(_entry("build", scope=ContextScope.private))
    moved = store.move_context_entry(
        project=PROJECT,
        theme="build",
        from_scope=ContextScope.private,
        to_scope=ContextScope.shared,
    )
    assert moved.scope is ContextScope.shared
    assert moved.full_content == created.full_content

    assert (
        store.get_context_entry(project=PROJECT, scope=ContextScope.private, theme="build")
        is None
    )
    assert (
        store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
        is not None
    )


def test_move_onto_an_occupied_scope_is_refused(store) -> None:
    store.create_context_entry(_entry("build", scope=ContextScope.private))
    store.create_context_entry(_entry("build", scope=ContextScope.shared))
    with pytest.raises(ValueError, match="already holds"):
        store.move_context_entry(
            project=PROJECT,
            theme="build",
            from_scope=ContextScope.private,
            to_scope=ContextScope.shared,
        )


def test_move_of_an_absent_entry_is_a_lookup_error(store) -> None:
    with pytest.raises(LookupError):
        store.move_context_entry(
            project=PROJECT,
            theme="absent",
            from_scope=ContextScope.private,
            to_scope=ContextScope.shared,
        )


def test_delete_tombstones_then_frees_the_theme_for_a_new_entry(store) -> None:
    first = store.create_context_entry(_entry("build"))
    store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")

    assert (
        store.get_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build") is None
    )
    assert store.list_context_entries(project=PROJECT) == []

    # The partial unique index is what makes this legal: a tombstone is not a live entry.
    second = store.create_context_entry(_entry("build"))
    assert second.id != first.id


def test_delete_is_idempotent_but_a_never_present_theme_is_a_lookup_error(store) -> None:
    store.create_context_entry(_entry("build"))
    store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")
    store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="build")

    with pytest.raises(LookupError):
        store.delete_context_entry(project=PROJECT, scope=ContextScope.shared, theme="never")


# --- the orientation map -----------------------------------------------------------


def test_a_project_starts_with_no_map(store) -> None:
    assert store.map_exists(project=PROJECT) is False
    assert store.get_map(project=PROJECT) is None


def test_the_map_is_created_once_and_rewritten_after(store) -> None:
    created = store.create_map(
        OrientationMap(project=PROJECT, full_content="# Map\n\nfirst\n", description="the map")
    )
    assert store.map_exists(project=PROJECT) is True
    assert store.get_map(project=PROJECT).full_content == created.full_content

    with pytest.raises(ValueError, match="already has"):
        store.create_map(OrientationMap(project=PROJECT, full_content="# Map\n\nsecond\n"))

    written = store.write_map(
        OrientationMap(project=PROJECT, full_content="# Map\n\nsecond\n", description="rewritten")
    )
    assert written.full_content == "# Map\n\nsecond\n"
    assert written.description == "rewritten"
    assert store.get_map(project=PROJECT).full_content == "# Map\n\nsecond\n"


def test_writing_a_map_that_does_not_exist_is_a_lookup_error(store) -> None:
    with pytest.raises(LookupError):
        store.write_map(OrientationMap(project=PROJECT, full_content="# Map\n"))
