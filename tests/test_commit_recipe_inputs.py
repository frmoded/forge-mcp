"""Tests for `forge_commit_recipe`'s new `inputs` param.

CW-forge-mcp-commit-recipe-accept-inputs-param (drain 2026-07-27-2005).

Wizard-authored interactive-exercise notes reference their input names
ONLY inside `{{ }}` slot bodies (invisible to auto-derivation). Pre-
drain, MCP had no way to author frontmatter `inputs:` on an action
note — `forge_create_note` hardcoded `inputs: []` and `forge_commit_
recipe` didn't touch inputs. This drain extends `commit_recipe` with
an optional `inputs=` param.

Tests:
  - fresh-note create + commit with inputs stamps frontmatter.
  - existing-note commit with inputs overwrites frontmatter.
  - commit without inputs (back-compat) leaves frontmatter untouched.
  - malformed inputs param → clean error.
  - end-to-end wizard-style workflow.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import commit_recipe, create_note, read_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"music": VaultFS(root=vault)})


@pytest.mark.asyncio
async def test_commit_recipe_with_inputs_stamps_frontmatter_fresh_note(
  single_vault_registry: VaultRegistry,
):
  """Fresh note (path doesn't exist) + inputs=[...] → frontmatter has
  `inputs: [name1, name2]` on disk."""
  result = await commit_recipe.run(
    arguments={
      "note_id": "exercises/name_this_scale",
      "source": "Return {{ guess }}.",
      "inputs": ["guess"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  # commit_recipe may fail its internal run step (which needs live
  # forge-transpile), but the write itself must succeed. So check on-
  # disk state directly, not the tool result's isError.
  vault_fs = single_vault_registry.get("music")
  content = (vault_fs.root / "exercises" / "name_this_scale.md").read_text()
  assert "inputs: [guess]" in content


@pytest.mark.asyncio
async def test_commit_recipe_with_inputs_overwrites_frontmatter_existing_note(
  single_vault_registry: VaultRegistry,
):
  """Existing note has inputs: [] (from create_note_shell) → commit
  with inputs=[guess] overwrites to inputs: [guess]."""
  # Create note first via forge_create_note (writes `inputs: []`).
  await create_note.run(
    arguments={"note_id": "exercises/guess", "description": "Guess it."},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "exercises" / "guess.md"
  assert "inputs: []" in path.read_text()

  await commit_recipe.run(
    arguments={
      "note_id": "exercises/guess",
      "source": "Return {{ guess }}.",
      "expected_version": 0,
      "inputs": ["guess"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  content = path.read_text()
  assert "inputs: [guess]" in content
  # Old value gone.
  assert "inputs: []" not in content


@pytest.mark.asyncio
async def test_commit_recipe_without_inputs_leaves_frontmatter_untouched(
  single_vault_registry: VaultRegistry,
):
  """Back-compat: no `inputs` arg → existing frontmatter inputs
  survives the commit. Pre-drain callers see zero change."""
  # Hand-write a note with `inputs: [tonic, mode]`.
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "seed.md"
  path.write_text(
    "---\n"
    "type: action\n"
    "inputs: [tonic, mode]\n"
    "recipe_version: 0\n"
    "---\n\n"
    "# Description\n\nSeed.\n"
  )
  # Commit a Recipe WITHOUT inputs arg.
  await commit_recipe.run(
    arguments={
      "note_id": "seed",
      "source": 'Return "hello".',
      "expected_version": 0,
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  content = path.read_text()
  # Wizard-authored inputs survived.
  assert "inputs: [tonic, mode]" in content
  # Recipe version bumped as expected.
  assert "recipe_version: 1" in content


@pytest.mark.asyncio
async def test_commit_recipe_empty_inputs_writes_empty_list(
  single_vault_registry: VaultRegistry,
):
  """`inputs=[]` means "clear to inputs: []" — distinct from None
  ("leave alone")."""
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "clear.md"
  path.write_text(
    "---\n"
    "type: action\n"
    "inputs: [old_field]\n"
    "recipe_version: 0\n"
    "---\n\n"
    "# Description\n\nx.\n"
  )
  await commit_recipe.run(
    arguments={
      "note_id": "clear",
      "source": 'Return 1.',
      "expected_version": 0,
      "inputs": [],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  content = path.read_text()
  assert "inputs: []" in content
  assert "old_field" not in content


@pytest.mark.asyncio
async def test_commit_recipe_rejects_malformed_inputs(
  single_vault_registry: VaultRegistry,
):
  """`inputs` must be list-of-string. Non-list or list-of-non-string
  → clean isError, no fs touch."""
  result = await commit_recipe.run(
    arguments={
      "note_id": "malformed",
      "source": "Return 1.",
      "inputs": "not-a-list",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "list of strings" in result["content"][0]["text"].lower()
  # File was not created.
  vault_fs = single_vault_registry.get("music")
  assert not (vault_fs.root / "malformed.md").exists()


@pytest.mark.asyncio
async def test_commit_recipe_rejects_inputs_list_with_non_strings(
  single_vault_registry: VaultRegistry,
):
  """Mixed-type list → clean isError."""
  result = await commit_recipe.run(
    arguments={
      "note_id": "mixed",
      "source": "Return 1.",
      "inputs": ["ok_name", 42],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "list of strings" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_commit_recipe_multiple_inputs_render_inline(
  single_vault_registry: VaultRegistry,
):
  """`inputs=["tonic","mode","depth"]` → `inputs: [tonic, mode, depth]`
  (inline YAML, comma-separated, bare identifiers)."""
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "multi.md"
  path.write_text(
    "---\nrecipe_version: 0\n---\n\n# Description\n\nx.\n"
  )
  await commit_recipe.run(
    arguments={
      "note_id": "multi",
      "source": "Return 1.",
      "expected_version": 0,
      "inputs": ["tonic", "mode", "depth"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert "inputs: [tonic, mode, depth]" in path.read_text()


@pytest.mark.asyncio
async def test_end_to_end_wizard_workflow_readback(
  single_vault_registry: VaultRegistry,
):
  """Wizard-style: commit with inputs, then read_note returns them."""
  await commit_recipe.run(
    arguments={
      "note_id": "exercises/name_this_scale",
      "source": "Return {{ guess }}.",
      "inputs": ["guess"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await read_note.run(
    arguments={"note_id": "exercises/name_this_scale"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  inputs = result["structuredContent"]["note"]["inputs"]
  assert inputs == ["guess"]
