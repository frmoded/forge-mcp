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
  """§5 test #8 — git-tracked vault: git rm + auto-commit
  (drain 2026-07-24-1500). Prior contract left the deletion staged; the
  drain flipped it to `git rm` + immediate `git commit`, returning the
  SHA.
  """
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
  sc = result["structuredContent"]
  assert sc["git_tracked"] is True
  assert not (vault_fs.root / "retire_me.md").exists()
  # Working tree should be CLEAN — the deletion was committed, not staged.
  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert status == "", f"expected clean tree, got: {status!r}"
  # git_sha is a real HEAD commit that touches retire_me.md.
  git_sha = sc["git_sha"]
  assert isinstance(git_sha, str) and len(git_sha) == 40, git_sha
  show = subprocess.run(
    ["git", "show", "--name-only", "--pretty=format:%H", git_sha],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert git_sha in show
  assert "retire_me.md" in show
  assert sc["message"] == "delete note retire_me"


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


@pytest.mark.asyncio
async def test_delete_note_handles_dirty_working_tree(
  git_vault_registry: VaultRegistry,
):
  """CW-forge-delete-note-handle-dirty-working-tree (drain 2026-07-23-1800)
  §4 Part A — git-tracked vault where the target file has unstaged
  working-tree modifications. Reproduces wizard's 2026-07-23 real-world
  failure: MCP client commits a note; plugin re-derives Python facet
  on disk without committing; next delete must NOT fail with `git rm
  failed: local modifications`.
  """
  await _make_note(git_vault_registry, "dirty_note", "original body")
  vault_fs = git_vault_registry.get()
  # Commit initial so the file exists in HEAD.
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )
  # Simulate the plugin re-deriving Python facet on disk (unstaged edit).
  note_path = vault_fs.root / "dirty_note.md"
  note_path.write_text(
    note_path.read_text(encoding="utf-8")
    + "\n# Python\n\nprint('re-derived')\n",
    encoding="utf-8",
  )
  # Sanity: git sees the file as modified before the delete.
  pre_status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert " M dirty_note.md" in pre_status or "M  dirty_note.md" in pre_status, (
    f"expected dirty_note.md to be modified before delete, got: {pre_status!r}"
  )

  # The action under test.
  result = await delete_note.run(
    arguments={"note_id": "dirty_note"},
    bearer="tok",
    vault_registry=git_vault_registry,
  )

  # Expected: clean success; file gone from disk; deletion staged.
  assert result["isError"] is False, (
    "delete_note failed on dirty working tree: "
    f"{result['content'][0]['text']!r}"
  )
  assert result["structuredContent"]["git_tracked"] is True
  assert result["structuredContent"]["note_id"] == "dirty_note"
  assert result["structuredContent"]["path"] == "dirty_note.md"
  assert not note_path.exists()
  # Drain 2026-07-24-1500: deletion now auto-commits — tree is clean.
  post_status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert post_status == "", f"expected clean tree, got: {post_status!r}"
  git_sha = result["structuredContent"]["git_sha"]
  assert isinstance(git_sha, str) and len(git_sha) == 40, git_sha


# -- CW-forge-mcp-delete-and-rename-note-auto-commit (drain 2026-07-24-1500) --


@pytest.mark.asyncio
async def test_delete_note_path_scoped_commit_ignores_unrelated_staged_changes(
  git_vault_registry: VaultRegistry,
):
  """§5 acceptance #2 — delete auto-commit is path-scoped. Unrelated
  staged changes elsewhere in the vault must NOT be included in the
  auto-commit. Verified via `git show --stat` showing exactly one file
  changed.
  """
  await _make_note(git_vault_registry, "target", "target body")
  await _make_note(git_vault_registry, "bystander", "bystander body")
  vault_fs = git_vault_registry.get()
  # Commit initial state so both files are in HEAD.
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )
  # Stage an unrelated change on the bystander note.
  bystander = vault_fs.root / "bystander.md"
  bystander.write_text(
    bystander.read_text(encoding="utf-8") + "\nunrelated edit\n",
    encoding="utf-8",
  )
  subprocess.run(
    ["git", "add", "bystander.md"], cwd=vault_fs.root, check=True
  )

  result = await delete_note.run(
    arguments={"note_id": "target"},
    bearer="tok",
    vault_registry=git_vault_registry,
  )
  assert result["isError"] is False
  git_sha = result["structuredContent"]["git_sha"]
  assert isinstance(git_sha, str) and len(git_sha) == 40

  # The auto-commit should touch ONLY target.md.
  stat = subprocess.run(
    ["git", "show", "--name-only", "--pretty=format:", git_sha],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout.strip().split("\n")
  changed = [f for f in stat if f.strip()]
  assert changed == ["target.md"], (
    f"expected commit to touch only target.md, got: {changed}"
  )
  # Bystander's staged change is still staged, still not committed.
  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert "M  bystander.md" in status, (
    f"expected bystander.md to remain staged, got: {status!r}"
  )


@pytest.mark.asyncio
async def test_delete_note_custom_message_is_honoured(
  git_vault_registry: VaultRegistry,
):
  """§5 acceptance #4 — caller-supplied `message` overrides the default."""
  await _make_note(git_vault_registry, "target", "body")
  vault_fs = git_vault_registry.get()
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )

  result = await delete_note.run(
    arguments={
      "note_id": "target",
      "message": "retire target: superseded by new_target",
    },
    bearer="tok",
    vault_registry=git_vault_registry,
  )
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["message"] == "retire target: superseded by new_target"
  subject = subprocess.run(
    ["git", "log", "-1", "--pretty=format:%s", sc["git_sha"]],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert subject == "retire target: superseded by new_target"
