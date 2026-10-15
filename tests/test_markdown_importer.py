"""The markdown importer: what it finds, and what it refuses to invent.

Built on a small tree written by the test rather than on the real memory store, so the assertions
are about the importer's rules and not about whatever the store happens to hold today.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermod.data_entities.project_context import ContextScope
from hermod.data_migrations.markdown_importer import (
    discover,
    frontmatter,
    import_markdown,
    main,
)
from hermod.data_repositories.record_project_repository import RecordProjectRepository
from tests.valaskjalf_schema import make_store

USER = "alvi"
ISSUER = "https://auth.lok.quest/oidc"
SUB = "import-operator"

pytestmark = pytest.mark.postgres


@pytest.fixture
def store() -> RecordProjectRepository:
    """A real store, with the tenant an import resolves and the two agents the tree names.

    `meta` and `other` are seeded because the test tree has private homes under `agent-meta` and
    `agent-other`, and a private write needs an agent row for its foreign key to point at.
    """
    url = os.getenv("HERMOD_PG_TEST_URL")
    if not url:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    make_store(url, user_id=USER, agents=("meta", "other"), identity=(ISSUER, SUB))
    return RecordProjectRepository(user_id=USER, db_url=url)

ENTRY = """---
project: "proj-a"
tags: [alpha, beta]
description: "what alpha is"
created: "2026-01-02"
updated: "2026-03-04"
---

# Alpha

The body.
"""

BARE = """# Gamma

No frontmatter at all.
"""

MAP = """---
project: "proj-a"
description: "the map for proj-a"
created: "2026-01-01"
last_full_scan: "2026-02-02"
---

# Orientation Map — proj-a
"""

INDEX = "# proj-a Project Context\n\n## Entries\n\n- [alpha.md](alpha.md)\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _memory_root(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    _write(root / "shared-memory" / "proj-a" / "context" / "context-index.md", INDEX)
    _write(root / "shared-memory" / "proj-a" / "context" / "orientation-map.md", MAP)
    _write(root / "shared-memory" / "proj-a" / "context" / "alpha.md", ENTRY)
    _write(root / "shared-memory" / "proj-a" / "context" / "beta.md", BARE)
    _write(root / "shared-memory" / "proj-b" / "context" / "context-index.md", INDEX)
    _write(root / "shared-memory" / "proj-b" / "context" / "gamma.md", BARE)
    # A project-context home: the marker is the index beside it.
    _write(root / "agent-meta" / "knowledge-base" / "proj-a" / "context-index.md", INDEX)
    _write(root / "agent-meta" / "knowledge-base" / "proj-a" / "delta.md", ENTRY)
    # General knowledge, not project context: the same shape with no index.
    _write(root / "agent-meta" / "knowledge-base" / "research" / "notes.md", BARE)
    # A directory with no context/ at all.
    _write(root / "shared-memory" / "not-a-project" / "README.md", "# nothing\n")
    return root


# --- reading -------------------------------------------------------------------------


def test_frontmatter_reads_scalars_and_inline_lists() -> None:
    meta = frontmatter(ENTRY)
    assert meta["project"] == "proj-a"
    assert meta["tags"] == ["alpha", "beta"]
    assert meta["description"] == "what alpha is"


def test_frontmatter_is_empty_without_a_block_or_when_unterminated() -> None:
    assert frontmatter(BARE) == {}
    assert frontmatter("---\nproject: x\n\n# Body\n") == {}


# --- discovery -----------------------------------------------------------------------


def test_discovery_skips_the_index_and_the_map(tmp_path: Path) -> None:
    entries, maps = discover(_memory_root(tmp_path))
    themes = sorted(e.path.stem for e in entries)
    assert themes == ["alpha", "beta", "delta", "gamma"]
    assert [m.project for m in maps] == ["proj-a"]


def test_discovery_uses_the_directory_for_the_project_and_scope(tmp_path: Path) -> None:
    entries, _ = discover(_memory_root(tmp_path))
    by_theme = {e.path.stem: e for e in entries}
    assert by_theme["alpha"].project == "proj-a"
    assert by_theme["alpha"].scope is ContextScope.shared
    assert by_theme["delta"].project == "proj-a"
    assert by_theme["delta"].scope is ContextScope.agent
    # The private entry's frontmatter also says proj-a; the directory is what is used.
    assert by_theme["gamma"].project == "proj-b"


def test_a_knowledge_base_directory_without_an_index_is_not_project_context(tmp_path: Path) -> None:
    entries, _ = discover(_memory_root(tmp_path))
    assert "notes" not in {e.path.stem for e in entries}


# --- importing -----------------------------------------------------------------------


def _by_theme(rows, theme: str):
    """The first row carrying a theme, or ``None``.

    The store serves lists and never a theme read, so a test that wants one row filters the
    listing the way a caller does.
    """
    return next((row for row in rows if row.theme == theme), None)


def test_import_writes_entries_and_the_map(tmp_path: Path, store) -> None:
    repo = store
    report = import_markdown(repo, _memory_root(tmp_path))

    assert report.entries_created == 4
    assert report.maps_created == 1
    assert report.failed == []

    entry = _by_theme(repo.list_shared_context(project="proj-a"), "alpha")
    assert entry is not None
    # The content is the whole document, frontmatter included.
    assert entry.full_content.startswith("---\nproject:")
    assert "The body." in entry.full_content
    # And the columns are a projection of that same frontmatter.
    assert entry.content_summary == "what alpha is"
    assert entry.tags == ["alpha", "beta"]
    assert entry.created_date == "2026-01-02"

    orientation_map = repo.get_map(project="proj-a")
    assert orientation_map is not None
    assert orientation_map.content_summary == "the map for proj-a"
    assert orientation_map.modified_date == "2026-02-02"


def test_an_entry_with_no_frontmatter_still_imports(tmp_path: Path, store) -> None:
    repo = store
    import_markdown(repo, _memory_root(tmp_path))

    entry = _by_theme(repo.list_shared_context(project="proj-b"), "gamma")
    assert entry is not None
    assert entry.content_summary is None
    assert entry.tags == []
    assert entry.full_content.startswith("# Gamma")


def test_a_second_run_is_a_no_op(tmp_path: Path, store) -> None:
    repo = store
    root = _memory_root(tmp_path)
    import_markdown(repo, root)

    again = import_markdown(repo, root)
    assert (again.entries_created, again.maps_created) == (0, 0)
    assert again.entries_skipped == 4
    assert again.maps_skipped == 1


def test_two_agents_writing_one_private_theme_are_two_rows(tmp_path: Path, store) -> None:
    """What the move was for: the agent is part of the identity, so nothing collides.

    The markdown layout keys private context by agent (`agent-<domain>/knowledge-base/<project>/`)
    and the store's `memory_record` carries `agent_id`, so one theme under two agents is two
    entries. Under the table this replaces, identity was `(project, scope, theme)`, so the second
    file had nowhere to go and the importer could only report the pair.
    """
    root = _memory_root(tmp_path)
    _write(root / "agent-other" / "knowledge-base" / "proj-a" / "context-index.md", INDEX)
    _write(
        root / "agent-other" / "knowledge-base" / "proj-a" / "delta.md",
        '---\nproject: "proj-a"\n---\n\n# The other agent\'s delta\n',
    )

    repo = store
    report = import_markdown(repo, root)

    assert report.entries_collided == 0
    assert report.collisions == []
    mine = _by_theme(repo.list_agent_context(project="proj-a", agent="meta"), "delta")
    theirs = _by_theme(repo.list_agent_context(project="proj-a", agent="other"), "delta")
    assert mine is not None and "The body." in mine.full_content
    assert theirs is not None and "other agent" in theirs.full_content


def test_a_dry_run_writes_no_rows(tmp_path: Path, store) -> None:
    repo = store
    report = import_markdown(repo, _memory_root(tmp_path), dry_run=True)

    assert report.entries_created == 4 and report.maps_created == 1
    assert repo.list_shared_context(project="proj-a") == []
    assert repo.get_map(project="proj-a") is None


def test_a_frontmatter_project_that_disagrees_is_reported_not_obeyed(tmp_path: Path, store) -> None:
    root = _memory_root(tmp_path)
    _write(
        root / "shared-memory" / "proj-b" / "context" / "stray.md",
        '---\nproject: "proj-a"\n---\n\n# Stray\n',
    )
    repo = store
    report = import_markdown(repo, root)

    assert len(report.project_mismatches) == 1
    assert "proj-a" in report.project_mismatches[0]
    # Imported under the directory it sits in, which is the addressing markdown itself uses.
    assert _by_theme(repo.list_shared_context(project="proj-b"), "stray") is not None


# --- the command line ----------------------------------------------------------------


def test_main_reports_a_missing_source_directory(tmp_path: Path, capsys) -> None:
    code = main(["--source", str(tmp_path / "nope"), "--iss", ISSUER, "--sub", SUB])
    assert code == 2
    assert "not a directory" in capsys.readouterr().out


def test_main_imports_into_the_tenant_the_pair_names(
    tmp_path: Path, monkeypatch, capsys, store
) -> None:
    monkeypatch.setenv("HERMOD_DB_URL", os.environ["HERMOD_PG_TEST_URL"])
    root = _memory_root(tmp_path)
    argv = ["--source", str(root), "--iss", ISSUER, "--sub", SUB]

    assert main([*argv, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "4 written" in out

    assert main(argv) == 0
    assert "imported" in capsys.readouterr().out
    # alpha and beta are shared for that project, delta is the same project's agent entry.
    assert len(store.list_shared_context(project="proj-a")) == 2
    assert len(store.list_agent_context(project="proj-a", agent="meta")) == 1
    assert store.get_map(project="proj-a") is not None


def test_main_refuses_a_pair_nobody_has_signed_in_with(
    tmp_path: Path, monkeypatch, capsys, store
) -> None:
    """End to end: the importer will not invent the tenant it writes for."""
    monkeypatch.setenv("HERMOD_DB_URL", os.environ["HERMOD_PG_TEST_URL"])
    root = _memory_root(tmp_path)
    code = main(["--source", str(root), "--iss", ISSUER, "--sub", "never-signed-in"])
    assert code == 2
    assert "refuses to invent" in capsys.readouterr().out
