"""CW-MCP-rename-delete-note — forge_rename_note tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.tools import create_note, rename_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture
def git_vault_registry(tmp_path: Path) -> VaultRegistry:
  """Vault that IS a git repo, so rename uses `git mv`."""
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
async def test_renames_note_happy_path(single_vault_registry: VaultRegistry):
  """§5 test #1 — plain rename on untracked vault."""
  await _make_note(single_vault_registry, "sketchpad", "scratch")

  result = await rename_note.run(
    arguments={"old_note_id": "sketchpad", "new_note_id": "hello_world"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert not (vault_fs.root / "sketchpad.md").exists()
  assert (vault_fs.root / "hello_world.md").is_file()
  # Content preserved.
  assert "scratch" in (vault_fs.root / "hello_world.md").read_text()
  # Structured result.
  sc = result["structuredContent"]
  assert sc["old_note_id"] == "sketchpad"
  assert sc["new_note_id"] == "hello_world"
  assert sc["new_path"] == "hello_world.md"
  assert sc["git_tracked"] is False


@pytest.mark.asyncio
async def test_renames_note_in_git_vault_uses_git_mv(
  git_vault_registry: VaultRegistry,
):
  """§5 test #2 — git-tracked vault: git mv + auto-commit
  (drain 2026-07-24-1500). Prior contract left the rename staged; the
  drain flipped it to `git mv` + immediate `git commit`, returning the
  SHA.
  """
  await _make_note(git_vault_registry, "sketchpad", "scratch")
  # Commit initial so `git mv` has something to move in HEAD.
  vault_fs = git_vault_registry.get()
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )

  result = await rename_note.run(
    arguments={"old_note_id": "sketchpad", "new_note_id": "hello_world"},
    bearer="tok",
    vault_registry=git_vault_registry,
  )
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["git_tracked"] is True
  # Working tree should be CLEAN — the rename was committed, not staged.
  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert status == "", f"expected clean tree, got: {status!r}"
  assert not (vault_fs.root / "sketchpad.md").exists()
  assert (vault_fs.root / "hello_world.md").is_file()
  # git_sha is a real HEAD commit that touches both old and new paths.
  git_sha = sc["git_sha"]
  assert isinstance(git_sha, str) and len(git_sha) == 40, git_sha
  show = subprocess.run(
    ["git", "show", "--name-only", "--pretty=format:%H", git_sha],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert git_sha in show
  assert "hello_world.md" in show
  assert sc["message"] == "rename note sketchpad → hello_world"


@pytest.mark.asyncio
async def test_rejects_missing_old_note(single_vault_registry: VaultRegistry):
  """§5 test #3 — old note doesn't exist → clean isError."""
  result = await rename_note.run(
    arguments={"old_note_id": "ghost", "new_note_id": "phantom"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_rejects_existing_new_note(single_vault_registry: VaultRegistry):
  """§5 test #4 — destination exists → clean isError; source preserved."""
  await _make_note(single_vault_registry, "src", "source body")
  await _make_note(single_vault_registry, "dst", "destination body")

  result = await rename_note.run(
    arguments={"old_note_id": "src", "new_note_id": "dst"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "already exists" in result["content"][0]["text"].lower()
  vault_fs = single_vault_registry.get()
  # Both survive; source is untouched.
  assert (vault_fs.root / "src.md").is_file()
  assert (vault_fs.root / "dst.md").is_file()
  assert "source body" in (vault_fs.root / "src.md").read_text()
  assert "destination body" in (vault_fs.root / "dst.md").read_text()


@pytest.mark.asyncio
async def test_rejects_path_traversal_in_old_id(
  single_vault_registry: VaultRegistry,
):
  """§5 test #5a — `../` in old_note_id refused before any fs op."""
  result = await rename_note.run(
    arguments={"old_note_id": "../etc/passwd", "new_note_id": "safe"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  vault_fs = single_vault_registry.get()
  assert list(vault_fs.root.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_path_traversal_in_new_id(
  single_vault_registry: VaultRegistry,
):
  """§5 test #5b — `../` in new_note_id refused; source stays put."""
  await _make_note(single_vault_registry, "src", "keep me")
  result = await rename_note.run(
    arguments={"old_note_id": "src", "new_note_id": "../outside"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  vault_fs = single_vault_registry.get()
  assert (vault_fs.root / "src.md").is_file()


@pytest.mark.asyncio
async def test_creates_parent_dirs_for_new_path(
  single_vault_registry: VaultRegistry,
):
  """§5 test #6 — renaming into a nested path creates parent dirs."""
  await _make_note(single_vault_registry, "top", "content")
  result = await rename_note.run(
    arguments={"old_note_id": "top", "new_note_id": "sub/deep/leaf"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get()
  assert (vault_fs.root / "sub" / "deep" / "leaf.md").is_file()


@pytest.mark.asyncio
async def test_normalizes_md_suffix_in_ids(
  single_vault_registry: VaultRegistry,
):
  """Caller may pass note_ids with .md; result normalizes them."""
  await _make_note(single_vault_registry, "src", "x")
  result = await rename_note.run(
    arguments={"old_note_id": "src.md", "new_note_id": "dst.md"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["old_note_id"] == "src"
  assert sc["new_note_id"] == "dst"


@pytest.mark.asyncio
async def test_rename_note_handles_dirty_working_tree(
  git_vault_registry: VaultRegistry,
):
  """CW-forge-rename-note-handle-dirty-working-tree (drain 2026-07-23-1905)
  §4 Part A — git-tracked vault where the SOURCE file has unstaged
  working-tree modifications. Sister-fix to drain 1800's delete-note
  handling: MCP client commits a note; plugin re-derives Python facet
  on disk without committing; next rename must (a) succeed and (b)
  produce a staged rename whose target content matches the HEAD source
  content, NOT the plugin's dirty re-derivation.
  """
  await _make_note(git_vault_registry, "old_name", "original body")
  vault_fs = git_vault_registry.get()
  # Commit initial so the source exists in HEAD.
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )
  # Capture the HEAD content of the source for later comparison.
  head_content = (vault_fs.root / "old_name.md").read_text(encoding="utf-8")

  # Simulate the plugin re-deriving Python facet on disk (unstaged edit).
  source_path = vault_fs.root / "old_name.md"
  source_path.write_text(
    head_content + "\n# Python\n\nprint('re-derived')\n",
    encoding="utf-8",
  )
  # Sanity: git sees source as modified before the rename.
  pre_status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert " M old_name.md" in pre_status or "M  old_name.md" in pre_status, (
    f"expected old_name.md to be modified before rename, got: {pre_status!r}"
  )

  # The action under test.
  result = await rename_note.run(
    arguments={"old_note_id": "old_name", "new_note_id": "new_name"},
    bearer="tok",
    vault_registry=git_vault_registry,
  )

  # Expected: clean success; new path exists; source gone.
  assert result["isError"] is False, (
    "rename_note failed on dirty working tree: "
    f"{result['content'][0]['text']!r}"
  )
  new_path = vault_fs.root / "new_name.md"
  assert new_path.exists(), "new_name.md should exist after rename"
  assert not source_path.exists(), "old_name.md should be gone after rename"

  # KEY assertion: new path's content matches the HEAD source content,
  # NOT the plugin's dirty edit. This is what fails absent the fix.
  assert new_path.read_text(encoding="utf-8") == head_content, (
    "rename produced target content != HEAD source content — the "
    "plugin's unstaged edit was silently included in the rename "
    "instead of being discarded"
  )

  # Drain 2026-07-24-1500: rename now auto-commits — tree is clean.
  post_status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert post_status == "", f"expected clean tree, got: {post_status!r}"
  git_sha = result["structuredContent"]["git_sha"]
  assert isinstance(git_sha, str) and len(git_sha) == 40, git_sha


# -- CW-forge-mcp-delete-and-rename-note-auto-commit (drain 2026-07-24-1500) --


@pytest.mark.asyncio
async def test_rename_note_path_scoped_commit_ignores_unrelated_staged_changes(
  git_vault_registry: VaultRegistry,
):
  """§5 acceptance #2 mirror — rename auto-commit is path-scoped."""
  await _make_note(git_vault_registry, "target", "target body")
  await _make_note(git_vault_registry, "bystander", "bystander body")
  vault_fs = git_vault_registry.get()
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

  result = await rename_note.run(
    arguments={"old_note_id": "target", "new_note_id": "renamed"},
    bearer="tok",
    vault_registry=git_vault_registry,
  )
  assert result["isError"] is False
  git_sha = result["structuredContent"]["git_sha"]
  assert isinstance(git_sha, str) and len(git_sha) == 40

  # The auto-commit should touch ONLY the old + new paths. `--name-status`
  # emits an R{score} row for renames (e.g. `R100\ttarget.md\trenamed.md`)
  # OR when the similarity heuristic misses, two rows `D target.md` +
  # `A renamed.md`. Either way, only those two filenames should appear —
  # NEVER `bystander.md`.
  status = subprocess.run(
    ["git", "show", "--name-status", "--pretty=format:", git_sha],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout.strip().splitlines()
  filenames = set()
  for row in status:
    if not row.strip():
      continue
    filenames.update(row.split("\t")[1:])
  assert filenames == {"target.md", "renamed.md"}, (
    f"expected commit to touch only target.md + renamed.md, "
    f"got filenames={filenames}, raw status={status}"
  )
  assert "bystander.md" not in filenames
  # Bystander's staged change remains staged.
  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert "M  bystander.md" in status, (
    f"expected bystander.md to remain staged, got: {status!r}"
  )


@pytest.mark.asyncio
async def test_rename_note_custom_message_is_honoured(
  git_vault_registry: VaultRegistry,
):
  """§5 acceptance #4 mirror — caller-supplied `message` overrides."""
  await _make_note(git_vault_registry, "sketchpad", "scratch")
  vault_fs = git_vault_registry.get()
  subprocess.run(["git", "add", "-A"], cwd=vault_fs.root, check=True)
  subprocess.run(
    ["git", "commit", "-q", "-m", "seed"], cwd=vault_fs.root, check=True
  )

  result = await rename_note.run(
    arguments={
      "old_note_id": "sketchpad",
      "new_note_id": "hello_world",
      "message": "wizard: promote sketchpad → hello_world",
    },
    bearer="tok",
    vault_registry=git_vault_registry,
  )
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["message"] == "wizard: promote sketchpad → hello_world"
  subject = subprocess.run(
    ["git", "log", "-1", "--pretty=format:%s", sc["git_sha"]],
    cwd=vault_fs.root, check=True, capture_output=True, text=True,
  ).stdout
  assert subject == "wizard: promote sketchpad → hello_world"
