"""Drain 2026-08-14-0330 — forge_create_asset tests.

Sibling of tests/test_asset_move_copy.py (drain 0250). Kept as its own file
because create_asset takes CALLER-SUPPLIED content rather than an existing
source path, so it shares no fixtures with the move/copy pair beyond the vault
scaffolding.

§5 coverage: text write, git-add staging, refuse-overwrite, disallowed
extension, traversal, and a base64 binary round-trip.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from forge_mcp.tools import create_asset
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L10 10"/></svg>'
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"binary-payload\x00\xff"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
  return subprocess.run(
    ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
  )


@pytest.fixture
def plain_vault(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture
def git_vault(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "gitvault"
  vault.mkdir()
  (vault / "seed.md").write_text("# seed\n")
  _git(vault.parent, "init", "-q", str(vault))
  _git(vault, "config", "user.email", "t@example.com")
  _git(vault, "config", "user.name", "Test")
  _git(vault, "add", "-A")
  _git(vault, "commit", "-q", "-m", "seed")
  return VaultRegistry({"default": VaultFS(root=vault)})


async def _create(reg, dest, content, encoding="text"):
  return await create_asset.run(
    arguments={
      "dest_path": dest,
      "content": content,
      "content_encoding": encoding,
    },
    bearer="tok",
    vault_registry=reg,
  )


# -- happy paths ------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_svg_from_text_content(plain_vault: VaultRegistry):
  """Wizard's actual use case: a hand-authored SVG written to a new path."""
  root = plain_vault.get().root
  result = await _create(plain_vault, "note/resources/images/frequency.svg", SVG)

  assert result["isError"] is False
  assert result["structuredContent"]["created"] is True
  written = root / "note" / "resources" / "images" / "frequency.svg"
  assert written.read_text() == SVG


@pytest.mark.asyncio
async def test_creates_parent_directories(plain_vault: VaultRegistry):
  """Destination parents are created, matching move/copy's behaviour."""
  root = plain_vault.get().root
  assert not (root / "deep").exists()
  result = await _create(plain_vault, "deep/nested/dir/timbre.svg", SVG)
  assert result["isError"] is False
  assert (root / "deep" / "nested" / "dir" / "timbre.svg").is_file()


@pytest.mark.asyncio
async def test_base64_binary_round_trip(plain_vault: VaultRegistry):
  """Binary assets go in base64 and must come back byte-identical."""
  root = plain_vault.get().root
  encoded = base64.b64encode(PNG_BYTES).decode("ascii")
  result = await _create(plain_vault, "img/cover.png", encoded, encoding="base64")

  assert result["isError"] is False
  assert (root / "img" / "cover.png").read_bytes() == PNG_BYTES


@pytest.mark.asyncio
async def test_git_vault_stages_the_new_file(git_vault: VaultRegistry):
  """§4 — git add on tracked vaults, NOT auto-committed."""
  root = git_vault.get().root
  result = await _create(git_vault, "img/duration.svg", SVG)

  assert result["isError"] is False
  assert result["structuredContent"]["staged"] is True

  status = subprocess.run(
    ["git", "-C", str(root), "status", "--porcelain"],
    capture_output=True, text=True, check=True,
  ).stdout
  assert "A  img/duration.svg" in status, status

  # Explicitly NOT committed — the caller decides when.
  log = subprocess.run(
    ["git", "-C", str(root), "log", "--oneline"],
    capture_output=True, text=True, check=True,
  ).stdout
  assert log.count("\n") == 1, f"expected only the seed commit, got: {log}"


@pytest.mark.asyncio
async def test_plain_vault_reports_not_staged(plain_vault: VaultRegistry):
  result = await _create(plain_vault, "img/octave.svg", SVG)
  assert result["isError"] is False
  assert result["structuredContent"]["staged"] is False


# -- refusals ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_refuses_to_overwrite_existing(plain_vault: VaultRegistry):
  """§4 — refuse-overwrite, full stop. No force flag in this drain."""
  root = plain_vault.get().root
  (root / "img").mkdir()
  (root / "img" / "taken.svg").write_text("ORIGINAL")

  result = await _create(plain_vault, "img/taken.svg", SVG)

  assert result["isError"] is True
  assert result["structuredContent"]["created"] is False
  # Original bytes untouched.
  assert (root / "img" / "taken.svg").read_text() == "ORIGINAL"


@pytest.mark.asyncio
async def test_refuses_disallowed_extension(plain_vault: VaultRegistry):
  """The allowlist is what stops this being a write-any-file primitive."""
  root = plain_vault.get().root
  result = await _create(plain_vault, "evil.py", "import os\n")
  assert result["isError"] is True
  assert not (root / "evil.py").exists()


@pytest.mark.asyncio
async def test_refuses_md_extension(plain_vault: VaultRegistry):
  """`.md` is a note, not an asset — use the note tools."""
  result = await _create(plain_vault, "note.md", "# hi\n")
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_refuses_traversal(plain_vault: VaultRegistry):
  root = plain_vault.get().root
  result = await _create(plain_vault, "../escape.svg", SVG)
  assert result["isError"] is True
  assert not (root.parent / "escape.svg").exists()


@pytest.mark.asyncio
async def test_refuses_invalid_base64(plain_vault: VaultRegistry):
  """Bad base64 must be a clear error, not a half-written file."""
  root = plain_vault.get().root
  result = await _create(plain_vault, "img/broken.png", "!!!not base64!!!", encoding="base64")
  assert result["isError"] is True
  assert not (root / "img" / "broken.png").exists()


@pytest.mark.asyncio
async def test_refuses_unknown_encoding(plain_vault: VaultRegistry):
  """§8 — encoding is explicit; an unrecognised value is refused rather
  than silently treated as text."""
  result = await _create(plain_vault, "img/x.svg", SVG, encoding="rot13")
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_missing_content_errors(plain_vault: VaultRegistry):
  result = await create_asset.run(
    arguments={"dest_path": "img/x.svg", "content_encoding": "text"},
    bearer="tok",
    vault_registry=plain_vault,
  )
  assert result["isError"] is True
