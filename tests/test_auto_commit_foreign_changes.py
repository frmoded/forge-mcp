"""Foreign-change detection in `VaultFS.auto_commit` (drain 2026-08-03-1540).

`git commit -- <path>` records that path's staged state wholesale, so a
concurrent write by another actor lands inside the tool's commit under
the tool's message. Drain 2026-07-31-1740 found that mechanism behind a
`sync_state` value nobody could account for. Driver adjudicated
detect-and-report over refuse-or-diverge: concurrent restamping by the
Obsidian plugin is normal, so failing the common case would be worse
than labelling it.

These tests pin the detection, not the absorption — the commit is still
wholesale by design.
"""
from __future__ import annotations

import subprocess

import pytest

from forge_mcp.vault_fs import VaultFS


def _git(root, *args):
  subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


@pytest.fixture
def vault(tmp_path):
  """A git-tracked vault with one committed note."""
  root = tmp_path / "vault"
  root.mkdir()
  _git(root, "init", "-q")
  _git(root, "config", "user.email", "test@local")
  _git(root, "config", "user.name", "test")
  _git(root, "config", "commit.gpgsign", "false")
  (root / "note.md").write_text("original\n", encoding="utf-8")
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "baseline")
  return VaultFS(root=root)


def test_clean_commit_reports_no_foreign_changes(vault):
  """The tool writes, nobody else touches it, nothing is flagged."""
  path = vault.root / "note.md"
  written = "tool wrote this\n"
  path.write_text(written, encoding="utf-8")

  result = vault.auto_commit(path, "tool: edit", expected_content=written)

  assert result.committed is True
  assert result.git_sha is not None
  assert result.foreign_changes_detected is False
  assert result.foreign_changes_summary is None


def test_concurrent_write_is_detected(vault):
  """Somebody else writes the same file between the tool's write and the
  commit — exactly wizard's `complete_this_scale_challenge` case."""
  path = vault.root / "note.md"
  written = "tool wrote this\n"
  path.write_text(written, encoding="utf-8")

  # The foreign actor. In the wild this is the Obsidian plugin
  # restamping sync_state reactively; here it is a plain write to the
  # same path before auto_commit stages it.
  path.write_text("tool wrote this\nplugin restamped this\n", encoding="utf-8")

  result = vault.auto_commit(path, "tool: edit", expected_content=written)

  assert result.committed is True
  assert result.foreign_changes_detected is True
  assert result.foreign_changes_summary is not None
  # The summary has to be actionable: name the SHA and how to look.
  assert result.git_sha[:8] in result.foreign_changes_summary
  assert "git show" in result.foreign_changes_summary
  # And it must not imply the tool's own change was lost.
  assert "did land" in result.foreign_changes_summary


def test_the_absorbed_change_really_is_in_the_commit(vault):
  """Guards the claim the summary makes. Detection would be worthless if
  the foreign line hadn't actually been committed — that is the whole
  reason this is report-only rather than refuse."""
  path = vault.root / "note.md"
  written = "tool wrote this\n"
  path.write_text(written, encoding="utf-8")
  path.write_text("tool wrote this\nplugin restamped this\n", encoding="utf-8")

  result = vault.auto_commit(path, "tool: edit", expected_content=written)

  show = subprocess.run(
    ["git", "-C", str(vault.root), "show", f"{result.git_sha}:note.md"],
    capture_output=True, text=True, check=True,
  )
  assert "plugin restamped this" in show.stdout
  assert result.foreign_changes_detected is True


def test_no_expected_content_skips_detection(vault):
  """Callers with nothing to compare against — deletes, renames — get a
  clean result rather than a false positive. False means 'none seen',
  not 'none possible', and this is the case that distinction is for."""
  path = vault.root / "note.md"
  path.write_text("changed by somebody\n", encoding="utf-8")

  result = vault.auto_commit(path, "tool: edit")

  assert result.committed is True
  assert result.foreign_changes_detected is False
  assert result.foreign_changes_summary is None


def test_untracked_vault_returns_uncommitted_defaults(vault, tmp_path):
  """No git, no commit, no detection — and crucially no exception: the
  file is on disk either way."""
  plain = tmp_path / "plain"
  plain.mkdir()
  path = plain / "note.md"
  path.write_text("body\n", encoding="utf-8")

  result = VaultFS(root=plain).auto_commit(path, "tool: edit", expected_content="body\n")

  assert result.committed is False
  assert result.git_sha is None
  assert result.foreign_changes_detected is False
  assert result.foreign_changes_summary is None
