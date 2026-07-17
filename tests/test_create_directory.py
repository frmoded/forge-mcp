"""CW-MCP-multi-vault-create-dir — forge_create_directory tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import create_directory
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture
def two_vault_registry(tmp_path: Path) -> tuple[VaultRegistry, Path, Path]:
  a = tmp_path / "alpha"
  b = tmp_path / "beta"
  a.mkdir()
  b.mkdir()
  return (
    VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)}),
    a,
    b,
  )


@pytest.mark.asyncio
async def test_creates_directory_in_vault(single_vault_registry: VaultRegistry):
  """Drain §5 test #7 — happy path."""
  result = await create_directory.run(
    arguments={"path": "experiments"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert (vault_fs.root / "experiments").is_dir()
  assert result["structuredContent"]["vault"] == "default"
  assert result["structuredContent"]["path"] == "experiments"


@pytest.mark.asyncio
async def test_idempotent_on_existing_dir(single_vault_registry: VaultRegistry):
  """Drain §5 test #8 — mkdir -p semantics; calling twice is fine."""
  await create_directory.run(
    arguments={"path": "twice"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await create_directory.run(
    arguments={"path": "twice"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert (vault_fs.root / "twice").is_dir()


@pytest.mark.asyncio
async def test_creates_nested_dirs(single_vault_registry: VaultRegistry):
  """Nested paths are created as parents."""
  result = await create_directory.run(
    arguments={"path": "outer/inner/deep"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert (vault_fs.root / "outer" / "inner" / "deep").is_dir()


@pytest.mark.asyncio
async def test_rejects_path_traversal(single_vault_registry: VaultRegistry):
  """Drain §5 test #9 — `../etc/passwd` rejected."""
  result = await create_directory.run(
    arguments={"path": "../etc/passwd"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert ".." in result["content"][0]["text"] or "traversal" in result["content"][0]["text"].lower() or "invalid" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_rejects_absolute_path(single_vault_registry: VaultRegistry):
  """Absolute paths reject cleanly."""
  result = await create_directory.run(
    arguments={"path": "/tmp/evil"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_rejects_hidden_segment(single_vault_registry: VaultRegistry):
  """Hidden segments (`.obsidian/config`) reject cleanly."""
  result = await create_directory.run(
    arguments={"path": ".obsidian/config"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_creates_in_named_vault_when_multi(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Drain §5 test #10 — vault=X targets the right vault; Y untouched."""
  reg, a, b = two_vault_registry
  await create_directory.run(
    arguments={"path": "in_beta", "vault": "beta"},
    bearer="tok",
    vault_registry=reg,
  )
  assert (b / "in_beta").is_dir()
  assert not (a / "in_beta").exists()


@pytest.mark.asyncio
async def test_unknown_vault_returns_error(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Passing an unregistered vault name surfaces the failure with the
  available list — no filesystem side effects."""
  reg, a, b = two_vault_registry
  result = await create_directory.run(
    arguments={"path": "x", "vault": "unknown"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is True
  assert "alpha" in result["content"][0]["text"]
  assert "beta" in result["content"][0]["text"]
  assert not (a / "x").exists()
  assert not (b / "x").exists()
