"""The config: local by default, verified when an issuer is configured.

The scaffold's test grew teeth with the hosting change. Its job is still the same — assert the
defaults a local server needs — plus the new property that matters: verification is decided by the
issuer's presence, so there is no mode switch to be set wrong.
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
    # The store's backend document, not markdown's: a served client has no local store.
    assert config.backend_doc == "storage-backends/db.md"
    assert config.db_path == Path("data/hermod.db")


def test_no_issuer_by_default_and_no_mode_switch() -> None:
    """The default run is local, and verification follows the issuer rather than a toggle.

    Munnin carries ``MUNNIN_AUTH`` because its laptop shape has a meaning the hosted shape does not
    (one constant tenant). Hermod has no tenant, so the switch would buy nothing and cost a state
    that can be set wrong; its absence is the guarantee that tokens are checked exactly when there
    is something to check them against.
    """
    config = Config()
    assert config.logto_endpoint == ""
    assert config.logto_audience == ()
    assert config.public_base_url == "http://127.0.0.1:8210"
    assert config.local_bind_all is False
    assert "auth_mode" not in Config.__dataclass_fields__


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("HERMOD_PORT", "9001")
    monkeypatch.setenv("HERMOD_CONTENT_ROOT", r"C:\Work\IM\somewhere-else")
    monkeypatch.setenv("HERMOD_BACKEND_DOC", "storage-backends/markdown.md")
    monkeypatch.setenv("HERMOD_LOGTO_ENDPOINT", "https://auth.lok.quest")
    monkeypatch.setenv("HERMOD_PUBLIC_BASE_URL", "https://hermod.lok.quest")
    monkeypatch.setenv(
        "HERMOD_LOGTO_AUDIENCE",
        "https://hermod.lok.quest/mcp, https://hermod.lok.quest/mcp/ ,",
    )
    config = load_config()
    assert config.port == 9001
    assert config.content_root == Path(r"C:\Work\IM\somewhere-else")
    assert config.backend_doc == "storage-backends/markdown.md"
    assert config.logto_endpoint == "https://auth.lok.quest"
    assert config.public_base_url == "https://hermod.lok.quest"
    # Comma-separated, trimmed, and empty entries dropped: the deploy writes a trailing comma.
    assert config.logto_audience == (
        "https://hermod.lok.quest/mcp",
        "https://hermod.lok.quest/mcp/",
    )


def test_local_bind_all_is_opt_in(monkeypatch) -> None:
    assert load_config().local_bind_all is False
    monkeypatch.setenv("HERMOD_LOCAL_BIND_ALL", "1")
    assert load_config().local_bind_all is True
