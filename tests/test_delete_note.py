"""CW-MCP-rename-delete-note — forge_delete_note tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.tools import create_note, delete_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture
def git_vault_registry(tmp_path: Path) -> VaultRegistry:
  """Vault that IS a git repo, so delete uses `git rm`."""
  vault = tmp_path / "gitvault"
  vault.mkdir()
  subprocess.run(["git", "init", "-q"], cwd=vault, check=True)
  subprocess.run(
    ["git", "config", "user.email", "test@example.com"], cwd=vault, check=True
  )
  subprocess.run(["git", "config", "user.name", "Test"], cwd=vault, check=True)
  return VaultRegistry({"default": VaultFS(root=vault)})


async def _make_note(reg: VaultRegistry, note_id: str, body: str = "hello") -> None:
  await create_note.run(
    arguments={"note_id": note_id, "description": body},
    bearer="tok",
    vault_registry=reg,
  )


@pytest.mark.asyncio
async def test_deletes_note_happy_path(single_vault_registry: VaultRegistry):
  """§5 test #7 — plain unlink on untracked vault."""
  await _make_note(single_vault_registry, "retire_me", "old body")
  result = await delete_note.run(
    arguments={"note_id": "retire_me"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert not (vault_fs.root / "retire_me.md").exists()
  sc = result["structuredContent"]
  assert sc["note_id"] == "retire_me"
  assert sc["path"] == "retire_me.md"
  assert sc["git_tracked"] is False


@pytest.mark.asyncio
async def test_deletes_note_in_git_vault_uses_git_rm(
  git_vault_registry: VaultRegistry,
):
  """§5 test #8 — git-tracked vault: git rm stages the deletion."""
  await _make_note(git_vault_registry, "retire_me", "old body")
  vault_fs = git_vault_registry.get()
  # Commit initial so the file exists in HEAD.
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )

  result = await delete_note.run(
    arguments={"note_id": "retire_me"},
    bearer="tok",
    vault_registry=git_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["git_tracked"] is True
  # File removed from disk; deletion is staged in git.
  assert not (vault_fs.root / "retire_me.md").exists()
  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  # `D` on the left column = staged deletion.
  assert status.startswith("D ") or "\nD " in status
  assert "retire_me.md" in status


@pytest.mark.asyncio
async def test_rejects_missing_note(single_vault_registry: VaultRegistry):
  """§5 test #9 — note doesn't exist → clean isError."""
  result = await delete_note.run(
    arguments={"note_id": "ghost"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_rejects_path_traversal(single_vault_registry: VaultRegistry):
  """§5 test #10 — `../` refused before any fs op."""
  # Even if nothing exists at the path, the traversal shape must be
  # rejected up-front (before disk access).
  result = await delete_note.run(
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
  """Caller may pass note_id with .md; result normalizes it."""
  await _make_note(single_vault_registry, "with_suffix", "body")
  result = await delete_note.run(
    arguments={"note_id": "with_suffix.md"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note_id"] == "with_suffix"
