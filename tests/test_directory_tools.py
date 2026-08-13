"""Drain 2026-08-14-0100 — forge_list_directory + forge_delete_directory tests.

§5.1 coverage map:
  (a) list empty existing dir      -> test_list_empty_dir_is_empty_not_error
  (b) list nonexistent dir         -> test_list_nonexistent_dir_errors
  (c) list mixed .md / non-.md     -> test_list_separates_files_and_dirs
  (d) create dir                   -> tests/test_create_directory.py (pre-existing)
  (e) create existing dir          -> tests/test_create_directory.py (pre-existing)
  (f) delete empty dir             -> test_delete_empty_dir_succeeds
  (g) delete non-empty dir         -> test_delete_non_empty_dir_errors_and_keeps_contents

The (a)/(b) pair is the specific ambiguity wizard hit: an empty directory and a
nonexistent one must NOT look alike to a caller.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import delete_directory, list_directory
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


# -- (a)/(b): empty vs nonexistent must be distinguishable -------------------


@pytest.mark.asyncio
async def test_list_empty_dir_is_empty_not_error(
  single_vault_registry: VaultRegistry,
):
  """§5.1(a) — an existing-but-empty dir lists successfully with no entries."""
  (single_vault_registry.get().root / "ccqa_scratch").mkdir()

  result = await list_directory.run(
    arguments={"path": "ccqa_scratch"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["exists"] is True
  assert sc["files"] == []
  assert sc["directories"] == []


@pytest.mark.asyncio
async def test_list_nonexistent_dir_errors(single_vault_registry: VaultRegistry):
  """§5.1(b) — a nonexistent dir is an error, clearly distinct from (a)."""
  result = await list_directory.run(
    arguments={"path": "no_such_dir"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is True
  assert result["structuredContent"]["exists"] is False
  assert "does not exist" in result["content"][0]["text"]


# -- (c): files vs subdirectories, .md vs non-.md ----------------------------


@pytest.mark.asyncio
async def test_list_separates_files_and_dirs(
  single_vault_registry: VaultRegistry,
):
  """§5.1(c) — files and subdirs are separated; extension is reported."""
  root = single_vault_registry.get().root
  mixed = root / "mixed"
  mixed.mkdir()
  (mixed / "note.md").write_text("# note\n")
  (mixed / "cover.png").write_bytes(b"\x89PNG\r\n")
  (mixed / "sub").mkdir()

  result = await list_directory.run(
    arguments={"path": "mixed"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["directories"] == ["sub"]

  by_name = {f["name"]: f for f in sc["files"]}
  assert set(by_name) == {"note.md", "cover.png"}
  assert by_name["note.md"]["extension"] == ".md"
  assert by_name["note.md"]["is_note"] is True
  assert by_name["cover.png"]["extension"] == ".png"
  assert by_name["cover.png"]["is_note"] is False


@pytest.mark.asyncio
async def test_list_is_not_recursive(single_vault_registry: VaultRegistry):
  """Listing one level only — nested content is not flattened into `files`."""
  root = single_vault_registry.get().root
  (root / "outer" / "inner").mkdir(parents=True)
  (root / "outer" / "inner" / "deep.md").write_text("x\n")
  (root / "outer" / "top.md").write_text("y\n")

  result = await list_directory.run(
    arguments={"path": "outer"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  sc = result["structuredContent"]
  assert [f["name"] for f in sc["files"]] == ["top.md"]
  assert sc["directories"] == ["inner"]


@pytest.mark.asyncio
async def test_list_rejects_traversal(single_vault_registry: VaultRegistry):
  """Path-traversal defense is inherited, not bypassed."""
  result = await list_directory.run(
    arguments={"path": "../escape"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True


# -- (f)/(g): delete only when empty ----------------------------------------


@pytest.mark.asyncio
async def test_delete_empty_dir_succeeds(single_vault_registry: VaultRegistry):
  """§5.1(f) — an empty dir is removed."""
  root = single_vault_registry.get().root
  (root / "gone").mkdir()

  result = await delete_directory.run(
    arguments={"path": "gone"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is False
  assert result["structuredContent"]["deleted"] is True
  assert not (root / "gone").exists()


@pytest.mark.asyncio
async def test_delete_non_empty_dir_errors_and_keeps_contents(
  single_vault_registry: VaultRegistry,
):
  """§5.1(g) — non-empty deletion errors clearly and deletes NOTHING."""
  root = single_vault_registry.get().root
  full = root / "full"
  full.mkdir()
  (full / "keep.md").write_text("# keep\n")

  result = await delete_directory.run(
    arguments={"path": "full"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is True
  assert result["structuredContent"]["deleted"] is False
  assert "not empty" in result["content"][0]["text"]
  # Nothing deleted — neither the dir nor its contents.
  assert (full / "keep.md").is_file()


@pytest.mark.asyncio
async def test_delete_non_empty_with_only_non_md_content_errors(
  single_vault_registry: VaultRegistry,
):
  """§4.4 — 'non-empty' means any content, not just Recipe notes."""
  root = single_vault_registry.get().root
  assets = root / "assets_only"
  assets.mkdir()
  (assets / "sound.mp3").write_bytes(b"ID3")

  result = await delete_directory.run(
    arguments={"path": "assets_only"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is True
  assert (assets / "sound.mp3").is_file()


@pytest.mark.asyncio
async def test_delete_non_empty_with_only_a_subdir_errors(
  single_vault_registry: VaultRegistry,
):
  """A lone subdirectory also counts as non-empty."""
  root = single_vault_registry.get().root
  (root / "has_sub" / "child").mkdir(parents=True)

  result = await delete_directory.run(
    arguments={"path": "has_sub"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )

  assert result["isError"] is True
  assert (root / "has_sub" / "child").is_dir()


@pytest.mark.asyncio
async def test_delete_nonexistent_dir_errors(
  single_vault_registry: VaultRegistry,
):
  """Deleting something that isn't there is an error, not a silent no-op."""
  result = await delete_directory.run(
    arguments={"path": "never_existed"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert result["structuredContent"]["deleted"] is False
