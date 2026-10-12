"""Bridge to the overlay's canonical composition logic.

Turning a procedure source into a self-contained Prompt takes two substitutions, and they are
split here by what the overlay actually publishes:

- **seam substitution** — ``storage-backends/seam.py``, a module, imported from the checked-out
  overlay at runtime through this bridge exactly as the overlay's own compiler imports it. No
  copy, so a served Prompt and an installed skill cannot drift on the seam's definitions.
- **component inlining** — carried in ``inline.py``, because the overlay keeps its inliner
  inside ``setup-scripts/compile-procedures.py``: a CLI script with a function in it, not a
  module. The fidelity test is what holds that copy honest.

Which ``*.md`` files are procedures is a third definition, and the overlay's rule is a bare
glob over ``procedures/``. That is the loader's one line rather than something to import.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType

_SEAM_REL = "storage-backends/seam.py"


@lru_cache(maxsize=None)
def _load(module_name: str, rel: str, content_root: str) -> ModuleType:
    """Load + cache one overlay module from a content root, by file path.

    ``content_root`` is the served overlay checkout. Raises ``FileNotFoundError`` when the
    checkout or the file is absent: a missing submodule must fail loud rather than serve a
    half-composed procedure, and the message names the path that was expected.
    """
    path = Path(content_root) / rel
    if not path.exists():
        raise FileNotFoundError(f"overlay module not found: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seam_compose(content_root: str) -> ModuleType:
    """The overlay's ``seam`` module — backend-section extraction and substitution."""
    return _load("hermod_seam", _SEAM_REL, content_root)
