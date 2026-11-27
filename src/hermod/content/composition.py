"""Procedure-scoped Format 2 views over independent, confined content snapshots."""

from __future__ import annotations

import re
from codecs import BOM_UTF8
from pathlib import Path

from procedure_composer import CompositionError, FileSource, MemorySource, compose
from procedure_composer.composer import Composition
from procedure_composer.document import Metadata, Reference, parse_document

from hermod.content.safety import safe_path

_COMPONENTS = "procedures/components"
_BACKENDS = "procedures/memory/storage-backends"


def is_format2_bytes(raw: bytes, path: Path) -> bool:
    """Recognize an exact opener; reject a misplaced metadata-like opener at the start."""
    lines = raw.splitlines()
    if lines and lines[0] == b"+++":
        return True
    lines = raw.removeprefix(BOM_UTF8).lstrip().splitlines()
    if lines and lines[0].rstrip() == b"+++":
        first = next(
            (line for line in lines[1:] if line.strip() and not line.lstrip().startswith(b"#")),
            b"",
        )
        if re.match(rb" {0,3}[a-z_][a-z0-9_-]*[ \t]*=", first):
            raise CompositionError(f"{path}: the +++ metadata opener must be alone on line 1")
    return False


def is_format2(path: Path) -> bool:
    return is_format2_bytes(path.read_bytes(), path)


def authored_metadata(path: Path, raw: bytes) -> Metadata | None:
    """Parse only the selected document's header, independently of its references."""
    if not is_format2_bytes(raw, path):
        return None
    lines = raw.splitlines(keepends=True)
    closing = next(
        (i for i in range(1, len(lines)) if lines[i].rstrip(b"\r\n") == b"+++"), None
    )
    header = b"".join(lines if closing is None else lines[: closing + 1])
    document = parse_document(f"procedures/{path.name}", header)
    if document.metadata.kind != "procedure" or document.metadata.id != path.stem:
        raise CompositionError(f"{path}: procedure identity or kind does not match its path")
    return document.metadata


def _procedure_view(
    path: Path, roots: tuple[Path, ...], backend_path: Path | None, backend: str
) -> tuple[MemorySource, tuple[str, ...]]:
    """Build a view of only the selected procedure and its reachable inputs.

    Component ownership follows the old loader: the procedure's own component directory
    first, then the first configured root with one; an absent component in that directory
    does not fall through to a later root. Backend ownership is chosen independently.
    """
    owner = path.parent.parent
    ordered = (owner, *(root for root in roots if root != owner))
    component_root = next(
        (
            root
            for root in ordered
            if safe_path(root, _COMPONENTS).is_dir() or safe_path(root, "components").is_dir()
        ),
        None,
    )
    snapshots: dict[Path, FileSource] = {}

    def snapshot(root: Path) -> FileSource:
        if root not in snapshots:
            snapshots[root] = FileSource(safe_path(root))
        return snapshots[root]

    proc_name = f"procedures/{path.name}"
    files: dict[str, bytes] = {proc_name: snapshot(owner).read(proc_name)}
    visiting: set[str] = set()
    missing: list[str] = []
    needs_backend = False

    def collect(source_path: str) -> None:
        nonlocal needs_backend
        if source_path in visiting:
            return  # The composer diagnoses cycles with their complete chain.
        visiting.add(source_path)
        document = parse_document(source_path, files[source_path])
        for node in document.nodes:
            if not isinstance(node, Reference) or backend not in node.applies_to:
                continue
            if node.kind == "storage":
                needs_backend = True
            elif node.ref not in files:
                provider = snapshot(component_root) if component_root is not None else None
                if provider is None or node.ref not in provider.paths(_COMPONENTS):
                    missing.append(Path(node.ref).stem)
                    continue
                files[node.ref] = provider.read(node.ref)
                collect(node.ref)

    collect(proc_name)
    if needs_backend and backend_path is not None:
        # The selected backend may be an explicitly configured legacy-relative location.
        # It is data only: neither the backend nor any code in the content root is imported.
        for root in sorted(roots, key=lambda item: len(item.parts), reverse=True):
            if backend_path.is_relative_to(root):
                provider = snapshot(root)
                relative = backend_path.relative_to(root).as_posix()
                data = (
                    provider.read(relative)
                    if relative.startswith("procedures/")
                    else backend_path.read_bytes()
                )
                files[f"{_BACKENDS}/{backend}.md"] = data
                break
    return MemorySource(files), tuple(dict.fromkeys(missing))


def missing_components(
    path: Path, roots: tuple[Path, ...], backend_path: Path | None, backend: str
) -> tuple[str, ...]:
    """Report every missing reachable component without hiding invalid authored references."""
    _, missing = _procedure_view(path, roots, backend_path, backend)
    return missing


def compose_procedure(
    path: Path, roots: tuple[Path, ...], backend_path: Path | None, backend: str
) -> Composition:
    """Render the procedure using the shared composer; missing references fail closed."""
    source, _ = _procedure_view(path, roots, backend_path, backend)
    return compose(path.stem, backend, source)
