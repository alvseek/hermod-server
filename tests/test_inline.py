"""The two rules the carried inliner has to keep.

Both came from the overlay's compiler and both are load-bearing: a component's header block must
not reach the wire, and a link that is not a component reference must survive.
"""

from __future__ import annotations

from pathlib import Path

from hermod.content.inline import component_body, inline_components

COMPONENT = """# Thing

*This is a component, not a standalone skill.*

---

The body starts here.
Second line.
"""

NO_RULE = """# Ruless

*Header only, no separating rule.*
"""


def _write(dirpath: Path, name: str, text: str) -> None:
    (dirpath / f"{name}.md").write_text(text, encoding="utf-8")


def test_body_is_everything_after_the_first_rule(tmp_path: Path) -> None:
    _write(tmp_path, "thing", COMPONENT)
    text, used, missing = inline_components("See [Thing]([root]/components/thing.md).", tmp_path)
    assert used == ["thing"]
    assert missing == []
    assert "not a standalone skill" not in text, "the header block reached the wire"
    assert "The body starts here." in text
    assert "Second line." in text


def test_the_component_link_becomes_its_label(tmp_path: Path) -> None:
    _write(tmp_path, "thing", COMPONENT)
    line = "Follow the [Thing component]([root]/components/thing.md) now."
    text, _, _ = inline_components(line, tmp_path)
    assert "[Thing component](" not in text
    assert text.startswith("Follow the Thing component now.")


def test_a_non_component_link_on_the_same_line_survives(tmp_path: Path) -> None:
    """The overlay's own in-document anchor must keep working in an installed skill."""
    _write(tmp_path, "thing", COMPONENT)
    line = "See [Thing]([root]/components/thing.md) and [Templates](#templates)."
    text, _, _ = inline_components(line, tmp_path)
    assert "[Templates](#templates)" in text


def test_a_component_with_no_rule_contributes_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "ruless", NO_RULE)
    assert component_body(NO_RULE) == []


def test_a_missing_component_is_reported_not_dropped(tmp_path: Path) -> None:
    text, used, missing = inline_components("See [Gone]([root]/components/gone.md).", tmp_path)
    assert used == []
    assert missing == ["gone"]
    assert "Gone" in text, "the reference should stay visible for the caller to surface"


def test_a_bare_mention_of_a_components_path_is_not_a_reference(tmp_path: Path) -> None:
    """The regex requires the closing paren, so prose about the folder is left alone."""
    text, used, missing = inline_components("Everything lives under components/thing.md", tmp_path)
    assert (used, missing) == ([], [])
    assert text == "Everything lives under components/thing.md\n"
