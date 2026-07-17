"""CW-MCP-runtime-vault-registration — forge_unregister_vault tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import unregister_vault
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def two_vault_registry(tmp_path: Path) -> VaultRegistry:
  a = tmp_path / "alpha"
  b = tmp_path / "beta"
  a.mkdir()
  b.mkdir()
  return VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)})


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "only"
  vault.mkdir()
  return VaultRegistry({"only": VaultFS(root=vault)})


@pytest.mark.asyncio
async def test_unregisters_existing_vault(two_vault_registry: VaultRegistry):
  """Drain §5 test #7 — 2 vaults, remove 1, 1 remains."""
  result = await unregister_vault.run(
    arguments={"name": "beta"},
    bearer="tok",
    vault_registry=two_vault_registry,
  )
  assert result["isError"] is False
  assert two_vault_registry.names() == ["alpha"]
  assert result["structuredContent"]["unregistered"] is True
  assert result["structuredContent"]["remaining_vaults"] == ["alpha"]


@pytest.mark.asyncio
async def test_rejects_unknown_name(two_vault_registry: VaultRegistry):
  """Drain §5 test #8 — unknown name → isError True; state unchanged."""
  result = await unregister_vault.run(
    arguments={"name": "gamma"},
    bearer="tok",
    vault_registry=two_vault_registry,
  )
  assert result["isError"] is True
  assert "not registered" in result["content"][0]["text"].lower()
  assert two_vault_registry.names() == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_refuses_last_vault_removal(single_vault_registry: VaultRegistry):
  """Drain §5 test #9 — safety invariant: last vault stays."""
  result = await unregister_vault.run(
    arguments={"name": "only"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "last remaining" in result["content"][0]["text"].lower() or "cannot remove" in result["content"][0]["text"].lower()
  # Vault still there.
  assert single_vault_registry.names() == ["only"]


@pytest.mark.asyncio
async def test_filesystem_untouched_on_unregister(
  two_vault_registry: VaultRegistry, tmp_path: Path,
):
  """Removing from registry must NOT delete the vault dir or its notes."""
  # Write a marker note in beta.
  vault_fs = two_vault_registry.get("beta")
  marker = vault_fs.root / "keep_me.md"
  marker.write_text("---\n---\n# Description\n")
  await unregister_vault.run(
    arguments={"name": "beta"},
    bearer="tok",
    vault_registry=two_vault_registry,
  )
  assert marker.exists()
