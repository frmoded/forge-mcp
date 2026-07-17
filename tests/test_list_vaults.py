"""CW-MCP-multi-vault-create-dir — forge_list_vaults tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import list_vaults
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.mark.asyncio
async def test_returns_all_registered_vaults(tmp_path: Path):
  """Drain §5 test #18 — every configured vault appears with metadata."""
  a = tmp_path / "alpha"
  b = tmp_path / "beta"
  a.mkdir()
  b.mkdir()
  reg = VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)})
  result = await list_vaults.run(
    arguments={}, bearer="tok", vault_registry=reg,
  )
  assert result["isError"] is False
  entries = result["structuredContent"]["vaults"]
  assert len(entries) == 2
  names = [e["name"] for e in entries]
  assert names == ["alpha", "beta"]  # insertion order preserved
  for e in entries:
    assert set(e.keys()) == {"name", "path", "note_count"}


@pytest.mark.asyncio
async def test_returns_empty_note_count_when_vault_empty(tmp_path: Path):
  """Drain §5 test #19 — a fresh vault reports 0 notes."""
  vault = tmp_path / "vault"
  vault.mkdir()
  reg = VaultRegistry({"default": VaultFS(root=vault)})
  result = await list_vaults.run(
    arguments={}, bearer="tok", vault_registry=reg,
  )
  assert result["structuredContent"]["vaults"][0]["note_count"] == 0


@pytest.mark.asyncio
async def test_returns_populated_note_count(tmp_path: Path):
  """Note count is `len(list_notes())` — verify it agrees with reality."""
  vault = tmp_path / "vault"
  vault.mkdir()
  # Two notes.
  (vault / "one.md").write_text("---\n---\n# Description\n")
  (vault / "two.md").write_text("---\n---\n# Description\n")
  reg = VaultRegistry({"default": VaultFS(root=vault)})
  result = await list_vaults.run(
    arguments={}, bearer="tok", vault_registry=reg,
  )
  assert result["structuredContent"]["vaults"][0]["note_count"] == 2
