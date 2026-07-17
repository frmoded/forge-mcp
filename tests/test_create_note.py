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
  """Drain §5 test #11 — happy path with Description body."""
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
  # Minimal V2a shape: frontmatter fences + Description.
  assert content.startswith("---\n")
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
