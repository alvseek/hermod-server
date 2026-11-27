"""Format 2 selection, confinement, and compatibility at the server adapter."""

from pathlib import Path

import pytest
from fastmcp import Client
from procedure_composer import CompositionError

from hermod.api_mcp.server import build_mcp
from hermod.business_services.awakening_service import AwakeningService
from hermod.business_services.context_service import ContextService
from hermod.content.loader import ContentLoader, ContentSource


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def document(name: str, kind: str, body: str) -> str:
    return (
        f'+++\nformat_version = 2\nid = "{name}"\nkind = "{kind}"\n'
        f'title = "{name}"\ndescription = "fixture"\n+++\n{body}'
    )


def component(root: Path, name: str, body: str) -> None:
    write(root, f"procedures/components/{name}.md", document(name, "component", body))


def proc(root: Path, name: str, body: str) -> None:
    write(root, f"procedures/{name}.md", document(name, "procedure", body))


def loader(*roots: Path, backend: str = "storage-backends/db.md") -> ContentLoader:
    return ContentLoader([ContentSource(str(i), root) for i, root in enumerate(roots)], backend)


def test_duplicate_procedure_and_component_ownership(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    ref = '@start component id="ref" ref="procedures/components/part.md"\n@end component\n'
    proc(first, "same", "# First\n\nFirst purpose.\n\n" + ref)
    proc(second, "same", "# Second\n\nSecond purpose.\n\n" + ref)
    component(first, "part", "FIRST COMPONENT\n")
    component(second, "part", "SECOND COMPONENT\n")
    actual = loader(first, second)
    assert actual.list_prompts() == ["same"]
    assert actual.title_prompt("same") == "same"
    assert actual.get_prompt("same").endswith("FIRST COMPONENT\n")
    assert actual.components_used("same") == ["part"]
    assert loader(second, first).get_prompt("same").endswith("SECOND COMPONENT\n")


def test_fallback_directory_is_selected_once_and_missing_is_not_hidden(tmp_path: Path) -> None:
    first, second, third = (tmp_path / name for name in ("first", "second", "third"))
    ref = '@start component id="ref" ref="procedures/components/part.md"\n@end component\n'
    proc(first, "example", "# Example\n\n" + ref)
    component(second, "part", "FROM SECOND\n")
    component(third, "part", "FROM THIRD\n")
    assert loader(first, second, third).get_prompt("example").endswith("FROM SECOND\n")
    (second / "procedures/components/part.md").unlink()
    actual = loader(first, second, third)
    assert actual.missing_components("example") == ("part",)
    with pytest.raises(CompositionError, match="missing component"):
        actual.get_prompt("example")


def test_legacy_component_directory_does_not_fall_through_to_other_root(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    proc(first, "p", '# P\n\n@start component id="ref" '
         'ref="procedures/components/part.md"\n@end component\n')
    write(first, "components/part.md", "# Legacy\n\n---\n\nOLD\n")
    component(second, "part", "WRONG FALLBACK\n")
    with pytest.raises(CompositionError, match="missing component"):
        loader(first, second).get_prompt("p")


def test_nested_components_and_unrelated_bad_documents(tmp_path: Path) -> None:
    root = tmp_path / "root"
    proc(root, "good", '# Good\n\n@start component id="one" '
         'ref="procedures/components/one.md"\n@end component\n')
    component(root, "one", 'ONE\n@start component id="two" '
              'ref="procedures/components/two.md"\n@end component\n')
    component(root, "two", "TWO\n")
    write(root, "procedures/bad.md", "+++\ninvalid TOML\n")
    write(root, "procedures/components/unrelated.md", "+++\ninvalid TOML\n")
    actual = loader(root)
    assert actual.list_prompts() == ["bad", "good"]
    assert actual.title_prompt("bad") == "bad"
    assert actual.describe_prompt("bad").startswith("Procedure 'bad'.")
    assert actual.title_prompt("good") == "good"
    assert actual.get_prompt("good") == "# Good\n\nONE\nTWO\n"
    with pytest.raises(CompositionError):
        actual.get_prompt("bad")


async def test_menu_does_not_compose_invalid_unrelated_documents(
    tmp_path: Path, monkeypatch
) -> None:
    proc(tmp_path, "good", "# Good\n\nGood purpose.\n")
    write(tmp_path, "procedures/bad.md", "+++\ninvalid TOML\n")

    def no_composition(*args):
        raise AssertionError("a menu must not compose procedures")

    def no_repository():
        raise AssertionError("a menu must not read the store")

    monkeypatch.setattr("hermod.content.loader.compose_procedure", no_composition)
    server = build_mcp(
        loader(tmp_path), ContextService(no_repository), AwakeningService(no_repository)
    )
    async with Client(server) as client:
        assert {p.name for p in await client.list_prompts()} == {"bad", "good"}
        rows = (await client.call_tool("list_procedures", {})).data
        assert {row["name"] for row in rows} == {"bad", "good"}


async def test_authored_metadata_reaches_public_mcp_menu_and_native_prompt(tmp_path: Path) -> None:
    def no_repository():
        raise AssertionError("content access must not use the store")

    def author(title: str, description: str, argument: str, help_text: str) -> None:
        write(
            tmp_path, "procedures/new-procedure.md",
            '+++\nformat_version = 2\nid = "new-procedure"\nkind = "procedure"\n'
            f'title = "{title}"\ndescription = "{description}"\n'
            f'argument_name = "{argument}"\nargument_description = "{help_text}"\n'
            '+++\n# Body heading\n\nBody purpose.\n\n$ARGUMENTS\n',
        )

    author("First title", "First purpose.", "topic", "Describe the topic")
    server = build_mcp(
        loader(tmp_path), ContextService(no_repository), AwakeningService(no_repository)
    )
    async with Client(server) as client:
        for title, description, argument, help_text in (
            ("First title", "First purpose.", "topic", "Describe the topic"),
            ("Updated title", "Updated purpose.", "subject", "Describe the subject"),
        ):
            author(title, description, argument, help_text)
            rows = (await client.call_tool("list_procedures", {})).data
            assert rows[0]["title"] == title
            assert rows[0]["description"].startswith(description + " Read with read_procedure")
            prompt = (await client.list_prompts())[0]
            assert prompt.title == title
            assert prompt.description == rows[0]["description"]
            assert prompt.arguments[0].name == argument
            assert help_text in prompt.arguments[0].description
            rendered = await client.get_prompt("new-procedure", {argument: "EXAMPLE"})
            assert rendered.messages[0].content.text.endswith("EXAMPLE\n")


@pytest.mark.parametrize("prefix", [b"\xef\xbb\xbf", b"\n", b"   "])
def test_misplaced_or_bom_metadata_is_not_served_as_legacy(tmp_path: Path, prefix: bytes) -> None:
    root = tmp_path / "root"
    raw = document("p", "procedure", "# Body\n\nText.\n").encode("utf-8")
    path = root / "procedures/p.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(prefix + raw)
    actual = loader(root)
    assert actual.title_prompt("p") == "p"
    for read in (actual.get_prompt, actual.components_used, actual.missing_components,
                 actual.unresolved_ops):
        with pytest.raises(CompositionError, match="metadata opener"):
            read("p")


@pytest.mark.parametrize("body", [
    "# Example\n\n```\n+++\nformat_version = 2\n+++\n```\n",
    "# Example\n\n> +++\n> format_version = 2\n> +++\n",
    "# Example\n\n    +++\n    format_version = 2\n    +++\n",
])
def test_legacy_prose_with_metadata_examples_stays_legacy(tmp_path: Path, body: str) -> None:
    write(tmp_path, "procedures/example.md", body)
    assert loader(tmp_path).get_prompt("example") == body


def test_exact_lookup_rejects_nonmembers_and_linked_procedures(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write(root, "procedures/safe.md", "# Safe\n\nSafe prose.\n")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\nSECRET\n", encoding="utf-8")
    link = root / "procedures/linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is unavailable")
    actual = loader(root)
    assert actual.list_prompts() == ["safe"]
    assert actual.title_prompt("safe") == "Safe"
    for name in ("linked", "../outside", "safe/../outside", r"safe\..\outside"):
        for read in (actual.get_prompt, actual.title_prompt, actual.missing_components,
                     actual.components_used, actual.unresolved_ops):
            with pytest.raises(KeyError):
                read(name)


def test_legacy_component_and_backend_paths_cannot_follow_links(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write(root, "procedures/p.md", "# P\n\n[Part](../components/part.md)\n")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n\n---\n\nSECRET\n", encoding="utf-8")
    (root / "components").mkdir()
    try:
        (root / "components/part.md").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is unavailable")
    for read in (loader(root).get_prompt, loader(root).components_used,
                 loader(root).missing_components):
        with pytest.raises(ValueError, match="linked content"):
            read("p")
    write(root, "procedures/q.md", "# Q\n\nOrdinary legacy text.\n")
    (root / "storage-backends").mkdir()
    (root / "storage-backends/db.md").symlink_to(outside)
    with pytest.raises(ValueError, match="linked content"):
        loader(root).get_prompt("q")


def test_missing_diagnostics_report_all_reachable_components(tmp_path: Path) -> None:
    proc(tmp_path, "p", '# P\n\n@start component id="one" '
         'ref="procedures/components/missing-one.md"\n@end component\n'
         '@start component id="two" ref="procedures/components/missing-two.md"\n@end component\n')
    assert loader(tmp_path).missing_components("p") == ("missing-one", "missing-two")
    with pytest.raises(CompositionError, match="missing component"):
        loader(tmp_path).get_prompt("p")


def test_backend_owner_and_override_independent_of_component_owner(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    proc(first, "p", '# P\n\n@start storage id="mechanics" '
         'ref="procedures/memory/storage-backends/{backend}.md" target="p"\n@end storage\n')
    for name, text in (("db", "DB CALL\n"), ("markdown", "FILE STEP\n")):
        write(second, f"procedures/memory/storage-backends/{name}.md",
              f'@start region id="p"\n{text}@end region\n')
    assert loader(first, second).get_prompt("p") == "# P\n\nDB CALL\n"
    assert loader(first, second, backend="storage-backends/markdown.md").get_prompt("p") == (
        "# P\n\nFILE STEP\n"
    )
    assert loader(
        first, second, backend="procedures/memory/storage-backends/markdown.md"
    ).get_prompt("p") == "# P\n\nFILE STEP\n"
    write(first, "custom/tools.md", '@start region id="p"\nCUSTOM\n@end region\n')
    assert loader(first, second, backend="custom/tools.md").get_prompt("p") == "# P\n\nCUSTOM\n"
    write(first, "custom/tools.md", '@start region id="other"\nX\n@end region\n')
    with pytest.raises(CompositionError, match="missing region"):
        loader(first, second, backend="custom/tools.md").get_prompt("p")


def test_converted_content_is_data_and_references_are_confined(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "root"
    proc(root, "p", "# P\n\nOrdinary content.\n")
    write(root, "storage-backends/seam.py", "raise AssertionError('content code executed')\n")
    write(root, "storage-backends/db.md", "not needed")

    def no_legacy_code(*args):
        raise AssertionError("legacy seam must not be loaded")

    monkeypatch.setattr("hermod.content.legacy_seam.compose_backend_section", no_legacy_code)
    assert loader(root).get_prompt("p") == "# P\n\nOrdinary content.\n"
    with pytest.raises(ValueError, match="relative"):
        loader(root, backend="../outside.md").get_prompt("p")


def test_legacy_independent_root_still_composes(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    write(root, "procedures/legacy.md", "# Legacy\n\n## Storage Mechanics\n\nplaceholder\n")
    write(root, "storage-backends/db.md", "## legacy\n\n### § op\n\nLEGACY TOOL\n")
    write(root, "storage-backends/seam.py",
          "STORAGE_MARKER = '## Storage Mechanics'\n"
          "def compose_backend_section(doc, name, components): return 'LEGACY TOOL'\n"
          "def substitute_storage_mechanics(core, section): return core + section\n")
    assert "LEGACY TOOL" in loader(root).get_prompt("legacy")


def test_legacy_procedure_with_converted_backend_is_data_only(tmp_path: Path) -> None:
    legacy, converted = tmp_path / "legacy", tmp_path / "converted"
    write(legacy, "procedures/legacy.md", "# Legacy\n\n## Storage Mechanics\n\nplaceholder\n")
    write(converted, "procedures/memory/storage-backends/db.md",
          '## legacy\n\n@start region id="legacy"\n### § op\n\nMIXED TOOL\n@end region\n')
    write(converted, "procedures/memory/storage-backends/seam.py",
          "raise AssertionError('content code executed')\n")
    assert "MIXED TOOL" in loader(legacy, converted).get_prompt("legacy")
