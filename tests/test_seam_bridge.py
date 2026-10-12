"""The seam bridge imports the overlay's own module, and fails loud when it cannot.

The seam is the half of the composition the server does *not* copy, so these tests pin both
directions: it loads the real module from the checkout, and a missing checkout raises rather than
serving a half-composed procedure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermod.content.seam_bridge import seam_compose

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "agent-memory-project"


def test_a_missing_checkout_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        seam_compose(str(tmp_path / "no-such-checkout"))
    assert "overlay module not found" in str(excinfo.value)
    assert "storage-backends" in str(excinfo.value)


def test_it_loads_the_overlays_own_seam() -> None:
    """The seam must come from the checkout, not from a copy inside this repo."""
    seam = seam_compose(str(CONTENT))
    names = (
        "STORAGE_MARKER",
        "has_seam",
        "compose_backend_section",
        "substitute_storage_mechanics",
    )
    for name in names:
        assert hasattr(seam, name), f"the loaded module is not the overlay's seam: no {name}"
    assert "storage-backends" in str(Path(seam.__file__))


def test_the_loaded_module_is_cached_per_root(tmp_path: Path) -> None:
    first = seam_compose(str(CONTENT))
    second = seam_compose(str(CONTENT))
    assert first is second
