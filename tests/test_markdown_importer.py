"""The markdown importer: what it finds, and what it refuses to invent.

Built on a small tree written by the test rather than on the real memory store, so the assertions
are about the importer's rules and not about whatever the store happens to hold today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermod.data_entities.project_context import ContextScope
from hermod.data_migrations.markdown_importer import (
    discover,
    frontmatter,
    import_markdown,
    main,
)
from hermod.data_repositories.sqlite_project_repository import SqliteProjectRepository

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
    assert by_theme["delta"].scope is ContextScope.private
    # The private entry's frontmatter also says proj-a; the directory is what is used.
    assert by_theme["gamma"].project == "proj-b"


def test_a_knowledge_base_directory_without_an_index_is_not_project_context(tmp_path: Path) -> None:
    entries, _ = discover(_memory_root(tmp_path))
    assert "notes" not in {e.path.stem for e in entries}


# --- importing -----------------------------------------------------------------------


def test_import_writes_entries_and_the_map(tmp_path: Path) -> None:
    repo = SqliteProjectRepository(tmp_path / "h.db")
    report = import_markdown(repo, _memory_root(tmp_path))

    assert report.entries_created == 4
    assert report.maps_created == 1
    assert report.failed == []

    entry = repo.get_context_entry(project="proj-a", scope=ContextScope.shared, theme="alpha")
    assert entry is not None
    # The content is the whole document, frontmatter included.
    assert entry.full_content.startswith("---\nproject:")
    assert "The body." in entry.full_content
    # And the columns are a projection of that same frontmatter.
    assert entry.description == "what alpha is"
    assert entry.tags == ["alpha", "beta"]
    assert entry.created_date == "2026-01-02"

    orientation_map = repo.get_map(project="proj-a")
    assert orientation_map is not None
    assert orientation_map.description == "the map for proj-a"
    assert orientation_map.modified_date == "2026-02-02"


def test_an_entry_with_no_frontmatter_still_imports(tmp_path: Path) -> None:
    repo = SqliteProjectRepository(tmp_path / "h.db")
    import_markdown(repo, _memory_root(tmp_path))

    entry = repo.get_context_entry(project="proj-b", scope=ContextScope.shared, theme="gamma")
    assert entry is not None
    assert entry.description is None
    assert entry.tags == []
    assert entry.full_content.startswith("# Gamma")


def test_a_second_run_is_a_no_op(tmp_path: Path) -> None:
    repo = SqliteProjectRepository(tmp_path / "h.db")
    root = _memory_root(tmp_path)
    import_markdown(repo, root)

    again = import_markdown(repo, root)
    assert (again.entries_created, again.maps_created) == (0, 0)
    assert again.entries_skipped == 4
    assert again.maps_skipped == 1


def test_two_agents_writing_one_private_theme_collide_and_are_reported(tmp_path: Path) -> None:
    """The store's identity has no agent in it, and the markdown layout does.

    `agent-[domain]/knowledge-base/[project]/` means two agents can each hold a private entry
    for one project under the same theme. One row is all the store has, so the second file
    cannot be written; the importer names the pair instead of counting the file as already
    present, because "a previous run wrote this" and "another agent's file owns this identity"
    are different facts.
    """
    root = _memory_root(tmp_path)
    _write(root / "agent-other" / "knowledge-base" / "proj-a" / "context-index.md", INDEX)
    _write(
        root / "agent-other" / "knowledge-base" / "proj-a" / "delta.md",
        '---\nproject: "proj-a"\n---\n\n# The other agent\'s delta\n',
    )

    repo = SqliteProjectRepository(tmp_path / "h.db")
    report = import_markdown(repo, root)

    assert report.entries_collided == 1
    assert len(report.collisions) == 1
    assert "agent-other" in report.collisions[0]
    assert "agent-meta" in report.collisions[0]
    # The first file read is the one kept, and it is the first because discovery sorts.
    kept = repo.get_context_entry(project="proj-a", scope=ContextScope.private, theme="delta")
    assert kept is not None
    assert "The body." in kept.full_content

    # A dry run sees the same collision, since claiming happens as files are read.
    report = import_markdown(SqliteProjectRepository(tmp_path / "other.db"), root, dry_run=True)
    assert report.entries_collided == 1


def test_a_dry_run_writes_no_rows(tmp_path: Path) -> None:
    repo = SqliteProjectRepository(tmp_path / "h.db")
    report = import_markdown(repo, _memory_root(tmp_path), dry_run=True)

    assert report.entries_created == 4 and report.maps_created == 1
    assert repo.list_context_entries(project="proj-a") == []
    assert repo.map_exists(project="proj-a") is False


def test_a_frontmatter_project_that_disagrees_is_reported_not_obeyed(tmp_path: Path) -> None:
    root = _memory_root(tmp_path)
    _write(
        root / "shared-memory" / "proj-b" / "context" / "stray.md",
        '---\nproject: "proj-a"\n---\n\n# Stray\n',
    )
    repo = SqliteProjectRepository(tmp_path / "h.db")
    report = import_markdown(repo, root)

    assert len(report.project_mismatches) == 1
    assert "proj-a" in report.project_mismatches[0]
    # Imported under the directory it sits in, which is the addressing markdown itself uses.
    assert repo.get_context_entry(project="proj-b", scope=ContextScope.shared, theme="stray")


# --- the command line ----------------------------------------------------------------


def test_main_reports_a_missing_source_directory(tmp_path: Path, capsys) -> None:
    assert main(["--source", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().out


def test_main_imports_into_the_configured_store(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HERMOD_DB_BACKEND", "sqlite")
    pytest.skip(
        "the CLI writes through the store, which this plan is replacing: it converts with the "
        "importer retarget in Phase 3"
    )
    monkeypatch.setenv("HERMOD_DB_PATH", str(tmp_path / "cli.db"))
    root = _memory_root(tmp_path)

    assert main(["--source", str(root), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "4 written" in out

    assert main(["--source", str(root)]) == 0
    assert "imported" in capsys.readouterr().out
    repo = SqliteProjectRepository(tmp_path / "cli.db")
    # alpha and beta are shared, delta is the private entry for the same project.
    assert len(repo.list_context_entries(project="proj-a")) == 3
    assert repo.map_exists(project="proj-a") is True
