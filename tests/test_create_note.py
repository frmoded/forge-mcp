"""CW-MCP-multi-vault-create-dir — forge_create_note tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import create_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.mark.asyncio
async def test_creates_empty_note_with_description(
  single_vault_registry: VaultRegistry,
):
  """Drain §5 test #11 — happy path with Description body.

  CW-create-note-shell-v2a-frontmatter: the shell now writes proper
  V2a frontmatter (`type: action` etc.), not `---\\n---\\n`.
  """
  result = await create_note.run(
    arguments={"note_id": "sketchpad", "description": "My scratch pad."},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  note_path = vault_fs.root / "sketchpad.md"
  assert note_path.is_file()
  content = note_path.read_text()
  # V2a shape: frontmatter fences + type/inputs/recipe_version + Description.
  assert content.startswith("---\n")
  assert "type: action" in content
  assert "# Description" in content
  assert "My scratch pad." in content
  # NO Recipe facet.
  assert "# Recipe" not in content


@pytest.mark.asyncio
async def test_creates_empty_note_without_description(
  single_vault_registry: VaultRegistry,
):
  """Empty Description is fine — just an empty section."""
  result = await create_note.run(
    arguments={"note_id": "empty"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  content = (vault_fs.root / "empty.md").read_text()
  assert "# Description" in content
  assert "# Recipe" not in content


@pytest.mark.asyncio
async def test_creates_note_in_nested_path(
  single_vault_registry: VaultRegistry,
):
  """Drain §5 test #12 — parent dir created as side effect."""
  result = await create_note.run(
    arguments={"note_id": "experiments/deep/dive"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert (vault_fs.root / "experiments" / "deep" / "dive.md").is_file()


@pytest.mark.asyncio
async def test_fails_when_note_exists(single_vault_registry: VaultRegistry):
  """Drain §5 test #13 — no overwrite of existing notes."""
  # Create the note once.
  await create_note.run(
    arguments={"note_id": "existing"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  # Second attempt fails cleanly.
  result = await create_note.run(
    arguments={"note_id": "existing"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "already exists" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_rejects_path_traversal(single_vault_registry: VaultRegistry):
  """Drain §5 test #14 — traversal is rejected before any write."""
  result = await create_note.run(
    arguments={"note_id": "../../etc/passwd"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  vault_fs = single_vault_registry.get()
  # Vault untouched.
  assert list(vault_fs.root.iterdir()) == []


@pytest.mark.asyncio
async def test_normalizes_md_suffix(single_vault_registry: VaultRegistry):
  """Caller may pass note_id with .md; result strips it in the response."""
  result = await create_note.run(
    arguments={"note_id": "with_suffix.md"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note_id"] == "with_suffix"


# ---------------------------------------------------------------------------
# CW-create-note-shell-v2a-frontmatter — proper V2a frontmatter shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_note_shell_writes_v2a_frontmatter(
  single_vault_registry: VaultRegistry,
):
  """§5 test #1 — the shell writes proper V2a frontmatter so the plugin
  detects it as a Forge action note. The exact opening block must be
  the canonical shape (`type: action`, `inputs: []`, `recipe_version:
  0`) to satisfy the plugin's `type: action | data` gate."""
  await create_note.run(
    arguments={"note_id": "mytest/greeting", "description": "Say hi"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  vault_fs = single_vault_registry.get()
  content = (vault_fs.root / "mytest" / "greeting.md").read_text()
  expected_prefix = (
    "---\n"
    "type: action\n"
    "inputs: []\n"
    "recipe_version: 0\n"
    "---\n"
    "\n"
    "# Description\n"
    "\n"
    "Say hi\n"
  )
  assert content == expected_prefix


@pytest.mark.asyncio
async def test_wizard_note_pipeline_end_to_end(
  single_vault_registry: VaultRegistry,
):
  """§5 test #4 — the wizard's actual flow: `forge_create_note` then
  `forge_commit_recipe`. Before this drain, the shell wrote an empty
  frontmatter that `parse_note` couldn't recognize; commit_recipe's
  splice_recipe then prepended its own frontmatter block, producing
  the `---\\nrecipe_version: 1\\n---\\n---\\n---\\n\\n# Description`
  artifact from drain §Symptom B. After the fix, there is EXACTLY ONE
  frontmatter block, `type: action` survives, and `recipe_version` was
  updated in place (0 → 1)."""
  from forge_mcp.vault_fs import VaultFS, parse_note

  await create_note.run(
    arguments={"note_id": "experiments/hello_world",
               "description": "Say hi to the world."},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  vault_fs = single_vault_registry.get()
  assert isinstance(vault_fs, VaultFS)
  # Now commit a Recipe. current_recipe_version reads 0 → new_version 1.
  vault_fs.commit_recipe(
    note_id="experiments/hello_world",
    new_recipe_body='[[print]] "Hello, world!".',
    expected_version=0,
  )
  content = (
    vault_fs.root / "experiments" / "hello_world.md"
  ).read_text()
  # Exactly ONE frontmatter block: 2 fence lines total.
  fence_count = content.count("\n---\n") + (
    2 if content.startswith("---\n") and "\n---\n" in content else 0
  )
  # Simpler + unambiguous: count `---` at line-start.
  starts_of_lines = [1 for line in content.split("\n") if line == "---"]
  assert len(starts_of_lines) == 2, (
    f"expected exactly 2 `---` fence lines (one open, one close), "
    f"got {len(starts_of_lines)}. Content:\n{content}"
  )
  # Plugin-detection: type field must be present + intact.
  assert "type: action" in content
  # Recipe version was bumped in-place.
  assert "recipe_version: 1" in content
  # And the Description + Recipe survived.
  assert "Say hi to the world." in content
  assert '[[print]] "Hello, world!".' in content
  # Parse round-trip: parse_note recognizes exactly ONE frontmatter block.
  parsed = parse_note(content)
  assert parsed.frontmatter_dict.get("type") == "action"
  assert parsed.frontmatter_dict.get("recipe_version") == "1"


@pytest.mark.asyncio
async def test_commit_recipe_preserves_extra_frontmatter_fields(
  single_vault_registry: VaultRegistry,
):
  """§5 test #3 — non-standard frontmatter fields survive the version-
  stamp update. Simulates a wizard-authored note with `inputs: [tonic]`
  + a description hash; after commit_recipe, those fields are still
  present and unchanged; only `recipe_version` bumps."""
  from forge_mcp.vault_fs import VaultFS

  vault_fs = single_vault_registry.get()
  assert isinstance(vault_fs, VaultFS)
  # Hand-place a note with a rich frontmatter (bypass create_note to
  # get non-default field shape).
  note_path = vault_fs.root / "seed.md"
  note_path.write_text(
    "---\n"
    "type: action\n"
    "inputs: [tonic]\n"
    "description_hash: abc123\n"
    "recipe_version: 0\n"
    "---\n"
    "\n# Description\n\nMake a scale.\n"
  )
  vault_fs.commit_recipe(
    note_id="seed",
    new_recipe_body="Return 1.",
    expected_version=0,
  )
  content = note_path.read_text()
  # Extra fields preserved verbatim.
  assert "inputs: [tonic]" in content
  assert "description_hash: abc123" in content
  # Version bumped in place.
  assert "recipe_version: 1" in content
  assert "recipe_version: 0" not in content
  # Still exactly one frontmatter block.
  assert content.count("\n---\n") == 1  # only the closing fence has \n---\n context
