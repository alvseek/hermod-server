"""The config is local-only by construction.

This is the scaffold's test: the package imports, the defaults are what a local server needs,
and the auth surface Munnin carries is genuinely absent rather than merely unused.
"""

from __future__ import annotations

from pathlib import Path

from hermod import __version__
from hermod.configuration.config import Config, load_config


def test_package_imports_with_a_version() -> None:
    assert __version__


def test_defaults_are_loopback_and_local() -> None:
    config = Config()
    assert config.host == "127.0.0.1"
    assert config.port == 8210
    assert config.content_root == Path("agent-memory-project")
    assert config.backend_doc == "storage-backends/markdown.md"
    assert config.db_path == Path("data/hermod.db")


def test_no_issuer_or_auth_surface() -> None:
    """Decision 5: local mode only, so there is no auth field to misconfigure.

    Munnin's config carries an issuer, an audience, a public base URL and a mode switch; a
    field that exists is a field that can be set wrong, so their absence is the guarantee.
    """
    fields = set(Config.__dataclass_fields__)
    forbidden = {
        "auth_mode",
        "public_base_url",
        "local_bind_all",
        "logto_endpoint",
        "logto_audience",
        "authkit_domain",
        "user_id",
    }
    assert not fields & forbidden, f"auth surface crept in: {sorted(fields & forbidden)}"


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("HERMOD_PORT", "9001")
    monkeypatch.setenv("HERMOD_CONTENT_ROOT", r"C:\Work\IM\somewhere-else")
    monkeypatch.setenv("HERMOD_BACKEND_DOC", "storage-backends/db.md")
    config = load_config()
    assert config.port == 9001
    assert config.content_root == Path(r"C:\Work\IM\somewhere-else")
    assert config.backend_doc == "storage-backends/db.md"
