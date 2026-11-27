"""Confined, non-linked paths for both legacy and Format 2 content reads."""

from pathlib import Path


def safe_path(root: Path, relative: Path | str = Path()) -> Path:
    """Reject traversal and every existing symlink or Windows reparse point on the path."""
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("content path must be relative to its configured root")
    absolute_root = root.absolute()
    candidate = absolute_root / relative
    for part in reversed((candidate, *candidate.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"linked content path: {part}")
    return root / relative
