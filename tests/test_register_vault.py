"""CW-MCP-runtime-vault-registration — forge_register_vault tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from forge_mcp.tools import register_vault
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  seed = tmp_path / "seed"
  seed.mkdir()
  return VaultRegistry({"default": VaultFS(root=seed)})


@pytest.mark.asyncio
async def test_registers_new_vault_happy_path(
  single_vault_registry: VaultRegistry, tmp_path: Path,
):
  """Drain §5 test #1 — dummy tmp dir, no name collision, success."""
  new = tmp_path / "new_vault"
  new.mkdir()
  result = await register_vault.run(
    arguments={"name": "scratch", "path": str(new)},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert "scratch" in single_vault_registry.names()
  assert result["structuredContent"]["registered_vault"]["name"] == "scratch"


@pytest.mark.asyncio
async def test_rejects_duplicate_name(
  single_vault_registry: VaultRegistry, tmp_path: Path,
):
  """Drain §5 test #2 — no silent overwrite; state unchanged on collision."""
  other = tmp_path / "other"
  other.mkdir()
  result = await register_vault.run(
    arguments={"name": "default", "path": str(other)},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "already registered" in result["content"][0]["text"].lower()
  # Existing "default" still points at the original seed dir.
  assert single_vault_registry.get("default").root != other.resolve()


@pytest.mark.asyncio
async def test_rejects_nonexistent_path(
  single_vault_registry: VaultRegistry, tmp_path: Path,
):
  """Drain §5 test #3 — path doesn't exist, isError True."""
  result = await register_vault.run(
    arguments={"name": "ghost", "path": str(tmp_path / "does_not_exist")},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "does not exist" in result["content"][0]["text"].lower()
  assert "ghost" not in single_vault_registry.names()


@pytest.mark.asyncio
async def test_rejects_file_path(
  single_vault_registry: VaultRegistry, tmp_path: Path,
):
  """Drain §5 test #4 — path is a file, not a directory."""
  file_path = tmp_path / "some_file.md"
  file_path.write_text("hello")
  result = await register_vault.run(
    arguments={"name": "afile", "path": str(file_path)},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "not a directory" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_rejects_relative_path(single_vault_registry: VaultRegistry):
  """Drain §5 test #5 — relative path rejected (no cwd ambiguity)."""
  result = await register_vault.run(
    arguments={"name": "rel", "path": "some/relative/path"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "not absolute" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_expands_tilde(
  single_vault_registry: VaultRegistry, tmp_path: Path, monkeypatch,
):
  """Drain §5 test #6 — `~/some/tmp/dir` gets expanded."""
  monkeypatch.setenv("HOME", str(tmp_path))
  target = tmp_path / "under_home"
  target.mkdir()
  result = await register_vault.run(
    arguments={"name": "tilde", "path": "~/under_home"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert single_vault_registry.get("tilde").root == target.resolve()


@pytest.mark.asyncio
async def test_rejects_readonly_path(
  single_vault_registry: VaultRegistry, tmp_path: Path,
):
  """Read-only dirs (chmod 0555) are rejected with a clear message."""
  ro = tmp_path / "readonly"
  ro.mkdir()
  ro.chmod(0o555)
  try:
    result = await register_vault.run(
      arguments={"name": "ro", "path": str(ro)},
      bearer="tok",
      vault_registry=single_vault_registry,
    )
    # Some CI environments run as root and pass os.access — skip in that case.
    if os.access(str(ro), os.W_OK):
      pytest.skip("running as root; os.access always returns True")
    assert result["isError"] is True
    assert "not writable" in result["content"][0]["text"].lower()
  finally:
    ro.chmod(0o755)  # cleanup so pytest can remove
