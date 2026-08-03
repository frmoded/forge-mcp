"""Drain 2026-08-03-1105 — forge_delete_note for non-.md vault assets.

Wizard could render an SVG via forge_render_viz but had no in-lane way
to remove it when the params were wrong; the driver had to `rm` by hand.
`is_asset=True` routes through VaultFS.asset_path, which skips the `.md`
coercion in note_path and enforces a media-extension allowlist so this
can never become a delete-any-file primitive.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.tools import delete_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def vault(tmp_path):
  root = (tmp_path / "assets-vault").resolve()
  root.mkdir()
  subprocess.run(["git", "init", "-q"], cwd=root, check=True)
  subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
  subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
  return root, VaultRegistry({"v": VaultFS(root=root)})


def _seed(root: Path, rel: str, body: bytes = b"x") -> Path:
  p = root / rel
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_bytes(body)
  subprocess.run(["git", "add", "-A"], cwd=root, check=True)
  subprocess.run(["git", "commit", "-qm", f"seed {rel}"], cwd=root, check=True)
  return p


async def _run(reg, **args):
  return await delete_note.run(args, bearer="tok", vault_registry=reg)


# --- criterion 2: default stays .md-only -----------------------------


@pytest.mark.asyncio
async def test_default_is_asset_false_still_deletes_md_notes(vault):
  root, reg = vault
  _seed(root, "notes/plain.md", b"# hi\n")

  result = await _run(reg, note_id="notes/plain")

  assert result.get("isError") is not True, result
  assert not (root / "notes/plain.md").exists()
  # `.md` id still reported without its extension, as before.
  assert result["structuredContent"]["note_id"] == "notes/plain"


@pytest.mark.asyncio
async def test_default_rejects_non_md_and_names_the_working_shape(vault):
  root, reg = vault
  _seed(root, "images/pitch.svg", b"<svg/>")

  result = await _run(reg, note_id="images/pitch.svg")

  assert result["isError"] is True
  # note_path appended `.md`, so it looked for images/pitch.svg.md.
  assert "not found" in result["content"][0]["text"]
  assert (root / "images/pitch.svg").exists(), "must not delete"


# --- criterion 3: every allowlisted extension ------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "ext",
  [".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif",
   ".mp3", ".mid", ".midi", ".wav"],
)
async def test_is_asset_accepts_each_allowlisted_extension(vault, ext):
  root, reg = vault
  rel = f"resources/asset{ext}"
  _seed(root, rel)

  result = await _run(reg, note_id=rel, is_asset=True)

  assert result.get("isError") is not True, result
  assert not (root / rel).exists()
  # Assets keep their extension in the reported id — pitch.svg and
  # pitch.mp3 are different files.
  assert result["structuredContent"]["note_id"] == rel


# --- criterion 4: everything else refused ----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", [
  "scripts/deploy.sh",
  "src/main.py",
  "forge.toml",
  "notes/README",       # no extension at all
  # .xml is excluded on purpose — no write tool emits it and it is just
  # as likely to be a config file as a MusicXML score.
  "data/config.xml",
])
async def test_is_asset_refuses_non_allowlisted_extensions(vault, rel):
  root, reg = vault
  _seed(root, rel)

  result = await _run(reg, note_id=rel, is_asset=True)

  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "not a deletable vault asset" in text
  assert "Allowed:" in text, "error should name the allowlist"
  assert (root / rel).exists(), f"{rel} must survive"


@pytest.mark.asyncio
async def test_is_asset_refuses_md_and_points_back_to_the_note_path(vault):
  root, reg = vault
  _seed(root, "notes/real.md", b"# hi\n")

  result = await _run(reg, note_id="notes/real.md", is_asset=True)

  assert result["isError"] is True
  assert "is a `.md` note, not an asset" in result["content"][0]["text"]
  assert (root / "notes/real.md").exists()


# --- criterion 5: path safety, both modes ----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
  "../escape.svg",
  "images/../../escape.svg",
  ".hidden/secret.svg",
  "images/.hidden.svg",
])
async def test_path_safety_enforced_with_is_asset(vault, bad):
  _root, reg = vault
  result = await _run(reg, note_id=bad, is_asset=True)

  assert result["isError"] is True
  assert "Invalid note_id" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_symlink_escape_refused_with_is_asset(vault, tmp_path):
  root, reg = vault
  outside = tmp_path / "outside.svg"
  outside.write_bytes(b"<svg/>")
  (root / "link.svg").symlink_to(outside)

  result = await _run(reg, note_id="link.svg", is_asset=True)

  assert result["isError"] is True
  assert outside.exists(), "file outside the vault must survive"


# --- criterion 6: git behaviour --------------------------------------


@pytest.mark.asyncio
async def test_asset_delete_auto_commits_and_returns_sha(vault):
  root, reg = vault
  _seed(root, "resources/images/pitch_sinewave_test.svg", b"<svg/>")

  result = await _run(
    reg, note_id="resources/images/pitch_sinewave_test.svg", is_asset=True,
  )

  assert result.get("isError") is not True, result
  sha = result["structuredContent"]["git_sha"]
  assert sha, "expected an auto-commit SHA"
  assert not (root / "resources/images/pitch_sinewave_test.svg").exists()

  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=root, check=True, capture_output=True, text=True,
  ).stdout
  assert status == "", f"tree should be clean, got {status!r}"

  # Success text should say "asset", not "note".
  assert "asset" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_missing_asset_reports_asset_not_note(vault):
  _root, reg = vault
  result = await _run(reg, note_id="resources/ghost.svg", is_asset=True)

  assert result["isError"] is True
  assert "asset 'resources/ghost.svg' not found" in result["content"][0]["text"]
