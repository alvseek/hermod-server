"""Import the overlay's markdown project memory into Hermod's store.

The one-shot path by which the markdown world becomes rows. The overlay's project memory has
lived as files (`shared-memory/[project]/context/*.md` and
`agent-[domain]/knowledge-base/[project]/*.md`); the store is now its home, so this reads the
files once and writes the two tables.

Four things it does deliberately:

- **The whole file is the entry's content.** Hermod's contract says an entry document is the
  file, frontmatter included, and the template's fenced block contains that frontmatter. Munnin's
  importer strips it instead, because its columns carry everything a record's reader needs. Copying
  that behaviour here would quietly drop the frontmatter from every entry and from every document
  read back out again.
- **`description` and `tags` are read *out* of that frontmatter as well**, because they are the
  frontmatter the context index line renders. The content keeps them too; the columns are a
  projection, not a replacement.
- **The directory names the project, never the frontmatter.** The path is the addressing the
  markdown backend itself uses (`[project-name]`), so a file whose frontmatter disagrees is
  imported under its directory and reported, rather than having the disagreement resolved silently.
- **It writes through the repository, not the service.** Every `§ op` the served backend names is
  gated on the template's `template_version` stamp, which is a rule for tool callers that must
  prove they read the template. Imported data predates that gate and carries its own shape, so the
  gate would refuse the entire import; the repository is the seam below it.

Idempotent by construction: an entry or map already present is skipped and counted, so a second run
is a no-op and a partially failed run can be repeated.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hermod.app import build_project_repository, validate_store
from hermod.business_services.tenant_resolver import TenantResolver
from hermod.configuration.config import load_config
from hermod.data_entities.project_context import ContextEntry, ContextScope, OrientationMap
from hermod.data_repositories.project_repository import ProjectRepository
from hermod.data_repositories.record_project_repository import MissingAgentError

#: The index file, which is a projection of the entries rather than one of them: the markdown
#: backend writes it beside them and the store derives it from the rows.
CONTEXT_INDEX_STEM = "context-index"
#: The map's filename inside a shared context directory. A map is its own entity, not an entry.
ORIENTATION_MAP_STEM = "orientation-map"
_FRONTMATTER_DELIM = "---"


@dataclass(frozen=True)
class EntrySource:
    """One markdown entry file, and the identity the store will hold it under.

    ``agent`` is the owner, and it is populated for the private scope only: the markdown path
    carries it (`agent-<domain>/knowledge-base/...`), so an importer reading files has to carry it
    across, exactly as the path did.
    """

    path: Path
    project: str
    scope: ContextScope
    agent: str | None = None


@dataclass(frozen=True)
class MapSource:
    """One markdown orientation map, and the project it belongs to."""

    path: Path
    project: str


@dataclass
class ImportReport:
    """What an import (or a dry run of one) found and did."""

    entries_created: int = 0
    entries_skipped: int = 0
    entries_collided: int = 0
    maps_created: int = 0
    maps_skipped: int = 0
    failed: list[str] = field(default_factory=list)
    project_mismatches: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"entries: {self.entries_created + self.entries_skipped} "
            f"({self.entries_created} written, "
            f"{self.entries_skipped - self.entries_collided} already present, "
            f"{self.entries_collided} colliding)",
            f"maps:    {self.maps_created + self.maps_skipped} "
            f"({self.maps_created} written, {self.maps_skipped} already present)",
        ]
        if self.collisions:
            out.append(
                f"entries two files both claim, so only the first is written "
                f"({len(self.collisions)}):"
            )
            out.extend(f"  {c}" for c in self.collisions)
        if self.project_mismatches:
            out.append(
                "frontmatter project disagrees with the directory "
                f"({len(self.project_mismatches)}):"
            )
            out.extend(f"  {m}" for m in self.project_mismatches)
        if self.failed:
            out.append(f"failed ({len(self.failed)}):")
            out.extend(f"  {f}" for f in self.failed)
        return out


# --- reading -------------------------------------------------------------------------


def frontmatter(text: str) -> dict[str, object]:
    """The metadata a document carries above its body, or ``{}`` when it carries none.

    A deliberate subset of YAML rather than a parser, the same subset Munnin's importer reads:
    the store authors a fixed shape, so a scalar (`project: x`) and an inline list
    (`tags: [a, b]`) are all that has to parse. Anything else is skipped rather than guessed at,
    because a wrong value (a project that is not the project) is worse than a missing one.

    Unlike Munnin's, this does **not** separate the body out: an entry's content is the whole
    document here, so the caller keeps the text and only reads the metadata out of it.
    """
    lines = text.lstrip("\ufeff").split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return {}
    close = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_DELIM), None
    )
    if close is None:
        return {}
    meta: dict[str, object] = {}
    for raw in lines[1:close]:
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value.strip("\"'")
    return meta


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def _dates(meta: dict[str, object], path: Path) -> tuple[str, str]:
    """``(created, modified)`` from the authored metadata, falling back to the file's mtime.

    The authored value is kept as written, which for most of these files is a bare date. Nothing
    orders by these columns (every read is a filtered list ordered by scope and theme), so
    normalising them would be inventing precision the files never had.
    """
    created = str(meta.get("created") or "").strip() or _mtime_iso(path)
    modified = (
        str(meta.get("updated") or meta.get("last_full_scan") or "").strip() or created
    )
    return created, modified


def parse_entry(source: EntrySource) -> ContextEntry:
    """The entry a markdown file holds, content kept whole."""
    text = source.path.read_text(encoding="utf-8")
    meta = frontmatter(text)
    created, modified = _dates(meta, source.path)
    tags = meta.get("tags")
    description = meta.get("description")
    return ContextEntry(
        project=source.project,
        scope=source.scope,
        theme=source.path.stem,
        full_content=text,
        content_summary=str(description) if description else None,
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
        created_date=created,
        modified_date=modified,
    )


def parse_map(source: MapSource) -> OrientationMap:
    """The orientation map a markdown file holds, content kept whole."""
    text = source.path.read_text(encoding="utf-8")
    meta = frontmatter(text)
    created, modified = _dates(meta, source.path)
    description = meta.get("description")
    return OrientationMap(
        project=source.project,
        full_content=text,
        content_summary=str(description) if description else None,
        created_date=created,
        modified_date=modified,
    )


# --- discovery -----------------------------------------------------------------------


def discover(source_root: Path) -> tuple[list[EntrySource], list[MapSource]]:
    """Every entry and map beneath ``source_root``, the memory store's own root.

    Two shapes, each with its own marker for "this directory is a project's home":

    - **shared** — `<root>/shared-memory/<project>/context/`, whose existence is the marker.
      `context-index.md` is the projection and `orientation-map.md` is the map, so neither is an
      entry.
    - **private** — `<root>/agent-<domain>/knowledge-base/<project>/`, marked by a
      `context-index.md` beside the entries. That file is what separates a project-context home
      from a general-knowledge one, which is the same test the memory store's own importer uses to
      keep project-scoped knowledge out of the core.
    """
    entries: list[EntrySource] = []
    maps: list[MapSource] = []

    shared = source_root / "shared-memory"
    if shared.is_dir():
        for project_dir in sorted(p for p in shared.iterdir() if p.is_dir()):
            context = project_dir / "context"
            if not context.is_dir():
                continue
            for path in sorted(context.glob("*.md")):
                if path.stem == CONTEXT_INDEX_STEM:
                    continue
                if path.stem == ORIENTATION_MAP_STEM:
                    maps.append(MapSource(path=path, project=project_dir.name))
                    continue
                entries.append(
                    EntrySource(
                        path=path, project=project_dir.name, scope=ContextScope.shared
                    )
                )

    for agent_dir in sorted(p for p in source_root.glob("agent-*") if p.is_dir()):
        knowledge = agent_dir / "knowledge-base"
        if not knowledge.is_dir():
            continue
        for project_dir in sorted(p for p in knowledge.iterdir() if p.is_dir()):
            if not (project_dir / f"{CONTEXT_INDEX_STEM}.md").is_file():
                continue
            for path in sorted(project_dir.glob("*.md")):
                if path.stem in {CONTEXT_INDEX_STEM, ORIENTATION_MAP_STEM}:
                    continue
                entries.append(
                    EntrySource(
                        path=path,
                        project=project_dir.name,
                        scope=ContextScope.agent,
                        # The folder is `agent-<domain>`; the store's agent id is the domain.
                        agent=agent_dir.name.removeprefix("agent-"),
                    )
                )

    return entries, maps


# --- importing -----------------------------------------------------------------------


def import_markdown(
    repo: ProjectRepository, source_root: Path, *, dry_run: bool = False
) -> ImportReport:
    """Write every discovered entry and map through ``repo``; report what happened.

    A read-only pass when ``dry_run`` is set, with one caveat that is not this function's doing:
    reading needs the record tables to exist, and nothing here creates them. Hermod issues no DDL
    (ADR-027), so a missing table or column surfaces on the first call rather than being applied —
    which means a dry run against a database the memory store has never migrated fails loudly
    instead of reporting counts.
    """
    entries, maps = discover(source_root)
    report = ImportReport()

    # Claimed as they are read, not as they are written, so two files wanting one row are caught
    # whether the run writes or not. The key is the whole identity, **agent included**: the store
    # carries `agent_id`, so the same theme under two agents is two entries rather than a
    # collision, which is what this importer used to have to report.
    claimed: dict[tuple[str, str, str | None, str], Path] = {}
    # What each project's layer already holds, read once per layer. The store checks no theme, so
    # not writing a second row for a theme that is already there is this importer's own job.
    present: dict[tuple[str, str | None], set[str]] = {}

    for source in entries:
        try:
            entry = parse_entry(source)
        except (OSError, UnicodeDecodeError) as exc:
            report.failed.append(f"{source.path}: {exc}")
            continue

        authored = frontmatter(entry.full_content).get("project")
        if authored and str(authored) != source.project:
            report.project_mismatches.append(
                f"{source.path}: frontmatter says {authored!r}, directory says "
                f"{source.project!r} (imported under the directory)"
            )

        identity = (entry.project, entry.scope.value, source.agent, entry.theme)
        holder = claimed.get(identity)
        if holder is not None:
            report.entries_collided += 1
            report.entries_skipped += 1
            report.collisions.append(f"{source.path} (already claimed by {holder})")
            continue
        claimed[identity] = source.path

        key = (entry.project, source.agent)
        if key not in present:
            present[key] = _present_themes(repo, entry.project, source.agent)

        try:
            if entry.theme in present[key]:
                report.entries_skipped += 1
            elif dry_run:
                report.entries_created += 1
            else:
                _create_entry(repo, entry, source.agent)
                present[key].add(entry.theme)
                report.entries_created += 1
        except MissingAgentError as exc:
            # The agent has no row in the store, so this entry was **not** written. It is a failure
            # rather than a skip: counting it as "already present" would let a run that wrote
            # nothing report success, and this count is what a verification reads.
            report.failed.append(f"{source.path}: {exc}")
        except ValueError:
            # A guard rather than a live branch: no store-level refusal reaches here today beyond a
            # missing agent, which the branch above takes. It stays because a refusal the store
            # adds later should count the file as skipped, which is what a taken identity meant
            # when the store still carried that rule.
            report.entries_skipped += 1
        except Exception as exc:  # noqa: BLE001 — a bad file must not end the run
            report.failed.append(f"{source.path}: {exc}")

    for source in maps:
        try:
            orientation_map = parse_map(source)
        except (OSError, UnicodeDecodeError) as exc:
            report.failed.append(f"{source.path}: {exc}")
            continue
        try:
            if repo.get_map(project=source.project) is not None:
                report.maps_skipped += 1
            elif dry_run:
                report.maps_created += 1
            else:
                repo.create_map(orientation_map)
                report.maps_created += 1
        except ValueError:
            report.maps_skipped += 1
        except Exception as exc:  # noqa: BLE001
            report.failed.append(f"{source.path}: {exc}")

    return report


def _create_entry(repo: ProjectRepository, entry: ContextEntry, agent: str | None) -> None:
    """Write one entry through the layer it belongs to.

    The two creates differ by the owner alone, which is the only branch here.
    """
    if entry.scope is ContextScope.agent:
        repo.create_agent_context_entry(entry, agent=agent)
    else:
        repo.create_shared_context_entry(entry)


def _present_themes(repo: ProjectRepository, project: str, agent: str | None) -> set[str]:
    """The themes a project's layer already holds, read once per layer.

    The store checks no theme, so idempotence is this importer's own job: a second run must not
    write a second row for a theme that is already there. A list is the only read the store
    serves, which makes this one call per layer rather than one per file.
    """
    entries = (
        repo.list_agent_context(project=project, agent=agent)
        if agent is not None
        else repo.list_shared_context(project=project)
    )
    return {entry.theme for entry in entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import the overlay's markdown project memory into Hermod's store."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="the memory root: the checkout holding shared-memory/ and agent-*/",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written and touch no rows",
    )
    parser.add_argument(
        "--iss",
        required=True,
        help="the issuer whose tenant this import writes for, e.g. https://auth.lok.quest/oidc",
    )
    parser.add_argument(
        "--sub",
        required=True,
        help="the subject of the account that owns the rows being imported",
    )
    args = parser.parse_args(argv)

    source_root = Path(args.source).expanduser()
    if not source_root.is_dir():
        print(f"not a directory: {source_root}")
        return 2

    # The store is decided by the environment, never by a flag: the DSN is a secret and does not
    # belong in a command line or in shell history. The *tenant* is named here instead, through the
    # same pair a login resolves, because this process has no token to read one from.
    config = load_config()
    validate_store(config)
    try:
        user_id = TenantResolver(db_url=config.db_url).resolve_existing(args.iss, args.sub)
    except LookupError as exc:
        print(exc)
        return 2
    repo = build_project_repository(config, user_id=user_id)
    print(f"tenant: {user_id}")
    print(f"source: {source_root}")

    report = import_markdown(repo, source_root, dry_run=args.dry_run)
    verb = "would be written" if args.dry_run else "written"
    print(f"dry run, nothing {verb}:" if args.dry_run else "imported:")
    for line in report.lines():
        print(f"  {line}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
