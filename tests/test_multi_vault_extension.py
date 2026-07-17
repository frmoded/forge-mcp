"""CW-MCP-multi-vault-create-dir — extension tests for existing tools.

Verifies commit_recipe + read_notes_in_vault respect the `vault` param
and default to the first-registered vault when unspecified.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import commit_recipe, read_notes_in_vault
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def two_vault_registry(tmp_path: Path) -> tuple[VaultRegistry, Path, Path]:
  a = tmp_path / "alpha"
  b = tmp_path / "beta"
  a.mkdir()
  b.mkdir()
  reg = VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)})
  return reg, a, b


@pytest.mark.asyncio
async def test_commit_recipe_targets_named_vault(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Drain §5 test #15 — commit to vault=beta lands in beta, alpha
  untouched."""
  reg, a, b = two_vault_registry
  result = await commit_recipe.run(
    arguments={"source": "Return 1.", "note_id": "test_note", "vault": "beta"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is False
  assert (b / "test_note.md").is_file()
  assert not (a / "test_note.md").exists()


@pytest.mark.asyncio
async def test_commit_recipe_defaults_to_first_vault(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Drain §5 test #17 — no `vault` arg → first-registered (alpha)."""
  reg, a, b = two_vault_registry
  result = await commit_recipe.run(
    arguments={"source": "Return 2.", "note_id": "default_target"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is False
  assert (a / "default_target.md").is_file()
  assert not (b / "default_target.md").exists()


@pytest.mark.asyncio
async def test_read_notes_in_vault_lists_from_named_vault(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Drain §5 test #16 — vault=beta returns beta's notes only."""
  reg, a, b = two_vault_registry
  # Put one note in each vault.
  (a / "alpha_note.md").write_text("---\n---\n# Description\n")
  (b / "beta_note.md").write_text("---\n---\n# Description\n")
  # Read from beta.
  result = await read_notes_in_vault.run(
    arguments={"vault": "beta"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is False
  notes = result["structuredContent"]["notes"]
  note_ids = [n["note_id"] for n in notes]
  assert note_ids == ["beta_note"]


@pytest.mark.asyncio
async def test_read_notes_in_vault_defaults_to_first_vault(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """No `vault` arg → first-registered (alpha)."""
  reg, a, b = two_vault_registry
  (a / "in_alpha.md").write_text("---\n---\n# Description\n")
  (b / "in_beta.md").write_text("---\n---\n# Description\n")
  result = await read_notes_in_vault.run(
    arguments={}, bearer="tok", vault_registry=reg,
  )
  notes = result["structuredContent"]["notes"]
  assert [n["note_id"] for n in notes] == ["in_alpha"]


@pytest.mark.asyncio
async def test_commit_recipe_unknown_vault_returns_error(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Passing an unregistered vault name surfaces cleanly — no writes."""
  reg, a, b = two_vault_registry
  result = await commit_recipe.run(
    arguments={"source": "Return 3.", "note_id": "n", "vault": "gamma"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is True
  assert not (a / "n.md").exists()
  assert not (b / "n.md").exists()
