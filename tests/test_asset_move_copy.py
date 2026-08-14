"""Drain 2026-08-14-0250 — forge_move_asset + forge_copy_asset tests.

§5 coverage map:
  1. move in git vault, git mv used (rename, not delete+add)
  2. copy in git vault, dest staged as added
  3. move/copy in non-git vault
  4. source missing            -> error, nothing changed
  5. dest exists               -> error, no overwrite, nothing changed
  6. traversal on source/dest  -> rejected
  7. dest parent missing       -> auto-created (decision documented in FEEDBACK)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.tools import copy_asset, move_asset
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

PNG = b"\x89PNG\r\n\x1a\n" + b"payload-bytes"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
  )


@pytest.fixture
def plain_vault(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  (vault / "resources" / "images").mkdir(parents=True)
  (vault / "resources" / "images" / "cover.png").write_bytes(PNG)
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture
def git_vault(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "gitvault"
  (vault / "resources" / "images").mkdir(parents=True)
  (vault / "resources" / "images" / "cover.png").write_bytes(PNG)
  _git(vault.parent, "init", "-q", str(vault))
  _git(vault, "config", "user.email", "t@example.com")
  _git(vault, "config", "user.name", "Test")
  _git(vault, "add", "-A")
  _git(vault, "commit", "-q", "-m", "seed")
  return VaultRegistry({"default": VaultFS(root=vault)})


async def _move(reg, src, dest):
  return await move_asset.run(
    arguments={"source_path": src, "dest_path": dest},
    bearer="tok",
    vault_registry=reg,
  )


async def _copy(reg, src, dest):
  return await copy_asset.run(
    arguments={"source_path": src, "dest_path": dest},
    bearer="tok",
    vault_registry=reg,
  )


# -- 1: git mv preserves history (rename, not delete+add) -------------------


@pytest.mark.asyncio
async def test_move_in_git_vault_uses_git_mv(git_vault: VaultRegistry):
  root = git_vault.get().root
  result = await _move(
    git_vault, "resources/images/cover.png", "note/resources/images/cover.png"
  )

  assert result["isError"] is False
  assert not (root / "resources/images/cover.png").exists()
  assert (root / "note/resources/images/cover.png").read_bytes() == PNG

  # A git-mv shows as a rename against the previous commit, not delete+add.
  out = subprocess.run(
    ["git", "-C", str(root), "show", "--name-status", "--format=", "HEAD"],
    capture_output=True, text=True, check=True,
  ).stdout
  assert out.startswith("R"), f"expected a rename status, got: {out!r}"


# -- 2: copy stages the destination ----------------------------------------


@pytest.mark.asyncio
async def test_copy_in_git_vault_stages_dest(git_vault: VaultRegistry):
  root = git_vault.get().root
  result = await _copy(
    git_vault, "resources/images/cover.png", "note/resources/images/cover.png"
  )

  assert result["isError"] is False
  # Both paths exist after a copy.
  assert (root / "resources/images/cover.png").read_bytes() == PNG
  assert (root / "note/resources/images/cover.png").read_bytes() == PNG

  status = subprocess.run(
    ["git", "-C", str(root), "status", "--porcelain"],
    capture_output=True, text=True, check=True,
  ).stdout
  assert "A  note/resources/images/cover.png" in status, status


# -- 3: non-git vaults fall back to plain filesystem ops -------------------


@pytest.mark.asyncio
async def test_move_in_plain_vault(plain_vault: VaultRegistry):
  root = plain_vault.get().root
  result = await _move(
    plain_vault, "resources/images/cover.png", "note/resources/images/cover.png"
  )
  assert result["isError"] is False
  assert not (root / "resources/images/cover.png").exists()
  assert (root / "note/resources/images/cover.png").read_bytes() == PNG


@pytest.mark.asyncio
async def test_copy_in_plain_vault(plain_vault: VaultRegistry):
  root = plain_vault.get().root
  result = await _copy(
    plain_vault, "resources/images/cover.png", "note/resources/images/cover.png"
  )
  assert result["isError"] is False
  assert (root / "resources/images/cover.png").read_bytes() == PNG
  assert (root / "note/resources/images/cover.png").read_bytes() == PNG


# -- 4: source missing ------------------------------------------------------


@pytest.mark.asyncio
async def test_move_missing_source_errors(plain_vault: VaultRegistry):
  root = plain_vault.get().root
  result = await _move(plain_vault, "resources/images/nope.png", "a/nope.png")
  assert result["isError"] is True
  assert not (root / "a").exists(), "must not create dirs for a failed move"


@pytest.mark.asyncio
async def test_copy_missing_source_errors(plain_vault: VaultRegistry):
  result = await _copy(plain_vault, "resources/images/nope.png", "a/nope.png")
  assert result["isError"] is True


# -- 5: no silent overwrite -------------------------------------------------


@pytest.mark.asyncio
async def test_move_existing_dest_errors_and_preserves_both(
  plain_vault: VaultRegistry,
):
  root = plain_vault.get().root
  (root / "note").mkdir()
  (root / "note" / "taken.png").write_bytes(b"ORIGINAL")

  result = await _move(plain_vault, "resources/images/cover.png", "note/taken.png")

  assert result["isError"] is True
  # Neither side touched.
  assert (root / "note" / "taken.png").read_bytes() == b"ORIGINAL"
  assert (root / "resources/images/cover.png").read_bytes() == PNG


@pytest.mark.asyncio
async def test_copy_existing_dest_errors_and_preserves_both(
  plain_vault: VaultRegistry,
):
  root = plain_vault.get().root
  (root / "note").mkdir()
  (root / "note" / "taken.png").write_bytes(b"ORIGINAL")

  result = await _copy(plain_vault, "resources/images/cover.png", "note/taken.png")

  assert result["isError"] is True
  assert (root / "note" / "taken.png").read_bytes() == b"ORIGINAL"


# -- 6: traversal defense on BOTH ends --------------------------------------


@pytest.mark.asyncio
async def test_traversal_rejected_on_source(plain_vault: VaultRegistry):
  result = await _move(plain_vault, "../escape.png", "note/x.png")
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_traversal_rejected_on_dest(plain_vault: VaultRegistry):
  result = await _move(plain_vault, "resources/images/cover.png", "../escape.png")
  assert result["isError"] is True
  # Source untouched when the dest is what's invalid.
  assert (plain_vault.get().root / "resources/images/cover.png").exists()


@pytest.mark.asyncio
async def test_non_asset_extension_rejected(plain_vault: VaultRegistry):
  """The asset allowlist still applies — this is not a move-any-file tool."""
  root = plain_vault.get().root
  (root / "secret.py").write_text("x = 1\n")
  result = await _move(plain_vault, "secret.py", "note/secret.py")
  assert result["isError"] is True
  assert (root / "secret.py").is_file()


# -- 7: dest parent auto-created --------------------------------------------


@pytest.mark.asyncio
async def test_move_auto_creates_dest_parents(plain_vault: VaultRegistry):
  """Wizard's use case: note/resources/{images,audio}/ do not exist yet."""
  root = plain_vault.get().root
  assert not (root / "note").exists()

  result = await _move(
    plain_vault, "resources/images/cover.png", "note/resources/images/cover.png"
  )

  assert result["isError"] is False
  assert (root / "note" / "resources" / "images" / "cover.png").is_file()


@pytest.mark.asyncio
async def test_copy_auto_creates_dest_parents(plain_vault: VaultRegistry):
  root = plain_vault.get().root
  result = await _copy(
    plain_vault, "resources/images/cover.png", "scales/resources/audio/cover.png"
  )
  assert result["isError"] is False
  assert (root / "scales" / "resources" / "audio" / "cover.png").is_file()
