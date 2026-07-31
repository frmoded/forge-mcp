"""CW-mcp-auto-commit-vanilla-and-asset-writes (drain 2026-07-31-1130).

Wizard reported it could not fulfil "commit + push the scaffold" because
none of the write tools committed. These pin that they now do — against a
REAL git repo, not a mock, because the whole failure mode was a tool that
wrote to disk and looked successful while git saw nothing.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.tools import create_markdown_note, edit_markdown_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


def _git(root: Path, *args: str) -> str:
  return subprocess.run(
    ["git", "-C", str(root), *args],
    capture_output=True, text=True, check=True,
  ).stdout.strip()


@pytest.fixture
def git_vault(tmp_path: Path) -> VaultRegistry:
  root = tmp_path / "vault"
  root.mkdir()
  _git(root, "init", "-q")
  _git(root, "config", "user.email", "test@example.invalid")
  _git(root, "config", "user.name", "Test")
  # Need one commit so HEAD exists.
  (root / ".gitkeep").write_text("", encoding="utf-8")
  _git(root, "add", ".gitkeep")
  _git(root, "commit", "-qm", "init")
  return VaultRegistry({"v": VaultFS(root=root)})


@pytest.fixture
def plain_vault(tmp_path: Path) -> VaultRegistry:
  root = tmp_path / "plain"
  root.mkdir()
  return VaultRegistry({"v": VaultFS(root=root)})


@pytest.mark.asyncio
async def test_create_markdown_note_commits_in_a_git_vault(git_vault):
  r = await create_markdown_note.run(
    arguments={"note_id": "theory/scaffold", "body": "# Scaffold\n",
               "vault": "v"},
    bearer="tok", vault_registry=git_vault,
  )
  assert r["isError"] is False, r
  sha = r["structuredContent"]["git_sha"]
  assert sha, "expected a commit SHA in a git-tracked vault"

  root = git_vault.get("v").root
  # The file is actually IN the commit, not merely on disk.
  tracked = _git(root, "ls-tree", "--name-only", "-r", "HEAD")
  assert "theory/scaffold.md" in tracked, tracked
  assert _git(root, "rev-parse", "HEAD") == sha
  # And the tree is clean — no leftover unstaged write.
  assert _git(root, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_edit_markdown_note_commits_the_edit(git_vault):
  await create_markdown_note.run(
    arguments={"note_id": "n", "body": "v1\n", "vault": "v"},
    bearer="tok", vault_registry=git_vault,
  )
  r = await edit_markdown_note.run(
    arguments={"note_id": "n", "body": "v2\n", "vault": "v"},
    bearer="tok", vault_registry=git_vault,
  )
  assert r["isError"] is False, r
  sha = r["structuredContent"]["git_sha"]
  assert sha
  root = git_vault.get("v").root
  assert _git(root, "show", f"{sha}:n.md") == "v2"
  assert _git(root, "status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_untracked_vault_still_writes_and_reports_none(plain_vault):
  """git_sha=None must mean *uncommitted*, never *unwritten*. A vault
  without git is a normal configuration, not an error."""
  r = await create_markdown_note.run(
    arguments={"note_id": "n", "body": "hello\n", "vault": "v"},
    bearer="tok", vault_registry=plain_vault,
  )
  assert r["isError"] is False, r
  assert r["structuredContent"]["git_sha"] is None
  written = Path(r["structuredContent"]["absolute_path"])
  assert written.read_text(encoding="utf-8").startswith("hello")
  assert "not git-tracked" in r["content"][0]["text"]


@pytest.mark.asyncio
async def test_success_text_names_the_commit(git_vault):
  """Wizard reads the text, not the structuredContent. If the commit
  isn't mentioned there, the gap this drain closes stays invisible."""
  r = await create_markdown_note.run(
    arguments={"note_id": "n", "body": "x\n", "vault": "v"},
    bearer="tok", vault_registry=git_vault,
  )
  sha = r["structuredContent"]["git_sha"]
  assert f"Committed {sha[:8]}." in r["content"][0]["text"]


@pytest.mark.asyncio
async def test_commit_scopes_to_the_written_file_only(git_vault):
  """An unrelated dirty file must not be swept into our commit —
  `_git_commit_file` passes explicit paths for exactly this reason."""
  root = git_vault.get("v").root
  (root / "unrelated.md").write_text("do not commit me\n", encoding="utf-8")
  r = await create_markdown_note.run(
    arguments={"note_id": "n", "body": "x\n", "vault": "v"},
    bearer="tok", vault_registry=git_vault,
  )
  sha = r["structuredContent"]["git_sha"]
  committed = _git(root, "show", "--name-only", "--format=", sha).split()
  assert committed == ["n.md"], committed
  # The unrelated file is still untracked, untouched.
  assert "unrelated.md" in _git(root, "status", "--porcelain")
