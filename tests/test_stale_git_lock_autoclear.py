"""Drain 2026-09-22-1830 — auto-clear a stale `.git/index.lock`.

music-theory's `.git/index.lock` sat live for days (since 2026-09-18):
`_git_commit_file` absorbed the failure silently (`git_sha: null`, no
exception — best-effort per drain §6), while `forge_delete_note`'s
pre-op `git checkout HEAD --` had no such absorption and raised
`VaultFSError` straight through. This drain adds `_clear_stale_git_lock`
(age-only staleness — git's lock file exposes no portable PID for a
liveness check) as a pre-flight at the top of every index-modifying git
call site in vault_fs.py, and a read-only `forge_check_git_lock`
diagnostic tool that reports lock state without touching anything.

Real temp git repos throughout, same discipline as
test_git_commit_outcome.py — "did the lock actually get cleared" is not
a question a mock can answer honestly.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from forge_mcp.tools import check_git_lock
from forge_mcp.vault_fs import (
  VaultFS,
  _clear_stale_git_lock,
  _git_commit_file,
  _git_lock_status,
)
from forge_mcp.vault_registry import VaultRegistry


def _git(cwd: Path, *args: str) -> str:
  return subprocess.run(
    ["git", "-C", str(cwd), *args],
    capture_output=True, text=True, check=True,
    env={
      "PATH": "/usr/bin:/bin:/usr/local/bin",
      "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@example.com",
      "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@example.com",
      "HOME": str(cwd),
    },
  ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
  root = tmp_path / "vault"
  root.mkdir()
  _git(root, "init", "-q", "-b", "main")
  (root / "note.md").write_text("original\n")
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "initial")
  return root


def _backdate_lock(repo: Path, age_seconds: int) -> Path:
  lock = repo / ".git" / "index.lock"
  lock.write_text("")
  old = time.time() - age_seconds
  os.utime(lock, (old, old))
  return lock


# --- _clear_stale_git_lock: the core primitive -------------------------


def test_no_lock_present_is_a_no_op(repo: Path):
  assert not (repo / ".git" / "index.lock").exists()
  cleared = _clear_stale_git_lock(repo)
  assert cleared is False


def test_stale_lock_gets_cleared(repo: Path):
  lock = _backdate_lock(repo, age_seconds=120)
  cleared = _clear_stale_git_lock(repo, max_age_seconds=60)
  assert cleared is True
  assert not lock.exists()


def test_fresh_lock_is_left_alone(repo: Path):
  lock = _backdate_lock(repo, age_seconds=1)
  cleared = _clear_stale_git_lock(repo, max_age_seconds=60)
  assert cleared is False
  assert lock.exists(), "a fresh (plausibly-live) lock must survive"


def test_threshold_is_exact_boundary_respecting(repo: Path):
  """age < max_age_seconds is the exact comparison — pin it, not just
  'roughly the right side'."""
  _backdate_lock(repo, age_seconds=59)
  assert _clear_stale_git_lock(repo, max_age_seconds=60) is False

  _backdate_lock(repo, age_seconds=61)
  assert _clear_stale_git_lock(repo, max_age_seconds=60) is True


# --- Prompt §7 test 1: stale lock -> commit succeeds, git_sha populated -


def test_stale_lock_commit_succeeds_and_populates_git_sha(repo: Path):
  """THE case music-theory hit: `_git_commit_file` used to silently
  return `git_sha: None` against this exact fixture (see
  test_git_commit_outcome.py's fresh-lock sibling, which still asserts
  the OLD behavior for a lock too young to clear)."""
  path = repo / "note.md"
  path.write_text("changed\n")
  _backdate_lock(repo, age_seconds=120)

  result = _git_commit_file(repo, path, "tool: edit note")

  assert result.outcome == "committed"
  assert result.git_sha is not None
  assert result.committed is True
  assert not (repo / ".git" / "index.lock").exists()


# --- Prompt §7 test 2: fresh lock -> unchanged today's-behavior failure -


def test_fresh_lock_commit_still_fails_same_as_before(repo: Path):
  """Non-regression: a fresh lock must still block, exactly matching
  test_git_commit_outcome.py's existing `git-error` assertion. If this
  drain's auto-clear ever started treating fresh locks as clearable,
  this test (and the pre-existing suite) would both go red."""
  path = repo / "note.md"
  path.write_text("changed\n")
  _backdate_lock(repo, age_seconds=1)

  result = _git_commit_file(repo, path, "tool: edit note")

  assert result.outcome == "git-error"
  assert result.git_sha is None


# --- Prompt §7 test 3: negative control, no lock -> no behavior change --


def test_no_lock_commit_behaves_exactly_as_before(repo: Path):
  path = repo / "note.md"
  path.write_text("changed\n")

  result = _git_commit_file(repo, path, "tool: edit note")

  assert result.outcome == "committed"
  assert result.git_sha is not None


# --- Every index-modifying call site gets the pre-flight ---------------


def test_delete_note_succeeds_past_a_stale_lock(repo: Path):
  """Pre-fix: this raised VaultFSError straight through, unlike
  _git_commit_file's silent absorption — the harder-failing half of
  the bug forge-core observed."""
  vfs = VaultFS(root=repo)
  _backdate_lock(repo, age_seconds=120)

  path, git_sha, _msg = vfs.delete_note("note")

  assert not path.exists()
  assert git_sha is not None
  assert not (repo / ".git" / "index.lock").exists()


def test_delete_note_still_raises_past_a_fresh_lock(repo: Path):
  from forge_mcp.vault_fs import VaultFSError

  vfs = VaultFS(root=repo)
  _backdate_lock(repo, age_seconds=1)

  with pytest.raises(VaultFSError):
    vfs.delete_note("note")


def test_rename_note_succeeds_past_a_stale_lock(repo: Path):
  """_git_aware_move — not one of the prompt's three named sites, but
  the exact same vulnerability (mirrors delete_note's pattern by
  design, per the code's own comment). See FEEDBACK for why this is
  in-scope."""
  vfs = VaultFS(root=repo)
  _backdate_lock(repo, age_seconds=120)

  path, git_sha, _msg = vfs.rename_note("note", "renamed")

  assert path.exists()
  assert not (repo / "note.md").exists()
  assert git_sha is not None
  assert not (repo / ".git" / "index.lock").exists()


def test_copy_asset_succeeds_past_a_stale_lock(repo: Path):
  vfs = VaultFS(root=repo)
  (repo / "img.svg").write_text("<svg/>")
  _git(repo, "add", "-A")
  _git(repo, "commit", "-q", "-m", "seed asset")
  _backdate_lock(repo, age_seconds=120)

  dst, staged = vfs.copy_asset("img.svg", "img2.svg")

  assert dst.exists()
  assert staged is True
  assert not (repo / ".git" / "index.lock").exists()


def test_create_asset_succeeds_past_a_stale_lock(repo: Path):
  vfs = VaultFS(root=repo)
  _backdate_lock(repo, age_seconds=120)

  dst, staged = vfs.create_asset("new.svg", b"<svg/>")

  assert dst.exists()
  assert staged is True
  assert not (repo / ".git" / "index.lock").exists()


# --- forge_check_git_lock: read-only, never clears ----------------------


def test_check_git_lock_reports_unlocked(repo: Path):
  vfs = VaultFS(root=repo)
  status = vfs.check_git_lock()
  assert status == {"locked": False, "age_seconds": None, "would_clear_next_write": False}


def test_check_git_lock_reports_stale_without_clearing_it(repo: Path):
  """The read-only diagnostic must NEVER clear anything itself — that's
  the whole reason it's a separate function from _clear_stale_git_lock,
  not a dry_run flag on it."""
  vfs = VaultFS(root=repo)
  lock = _backdate_lock(repo, age_seconds=120)

  status = vfs.check_git_lock()

  assert status["locked"] is True
  assert status["age_seconds"] >= 120
  assert status["would_clear_next_write"] is True
  assert lock.exists(), "the read-only check must not have cleared it"


def test_check_git_lock_reports_fresh_as_not_yet_clearable(repo: Path):
  vfs = VaultFS(root=repo)
  _backdate_lock(repo, age_seconds=1)

  status = vfs.check_git_lock()

  assert status["locked"] is True
  assert status["would_clear_next_write"] is False


def test_check_git_lock_on_non_git_vault_reports_unlocked(tmp_path: Path):
  root = tmp_path / "plain"
  root.mkdir()
  vfs = VaultFS(root=root)
  assert vfs.check_git_lock() == {
    "locked": False, "age_seconds": None, "would_clear_next_write": False,
  }


def test_git_lock_status_helper_matches_the_method(repo: Path):
  """Non-vacuity for the module-level helper the method delegates to.
  Compared structurally, not by exact float equality — each call reads
  `time.time()` independently, microseconds apart."""
  _backdate_lock(repo, age_seconds=120)
  a = _git_lock_status(repo)
  b = VaultFS(root=repo).check_git_lock()
  assert a["locked"] == b["locked"] is True
  assert a["would_clear_next_write"] == b["would_clear_next_write"] is True
  assert abs(a["age_seconds"] - b["age_seconds"]) < 1.0


# --- forge_check_git_lock MCP tool --------------------------------------


@pytest.mark.asyncio
async def test_tool_reports_lock_state(repo: Path):
  reg = VaultRegistry({"v": VaultFS(root=repo)})
  _backdate_lock(repo, age_seconds=120)

  result = await check_git_lock.run(
    arguments={"vault": "v"}, bearer="tok", vault_registry=reg,
  )

  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["vault"] == "v"
  assert sc["locked"] is True
  assert sc["would_clear_next_write"] is True
  assert (repo / ".git" / "index.lock").exists(), "the tool call must not have cleared it"


@pytest.mark.asyncio
async def test_tool_reports_no_lock(repo: Path):
  reg = VaultRegistry({"v": VaultFS(root=repo)})

  result = await check_git_lock.run(
    arguments={"vault": "v"}, bearer="tok", vault_registry=reg,
  )

  assert result["isError"] is False
  assert result["structuredContent"]["locked"] is False


@pytest.mark.asyncio
async def test_tool_defaults_to_first_registered_vault(repo: Path):
  reg = VaultRegistry({"only": VaultFS(root=repo)})

  result = await check_git_lock.run(arguments={}, bearer="tok", vault_registry=reg)

  assert result["isError"] is False
  assert result["structuredContent"]["vault"] == "only"


@pytest.mark.asyncio
async def test_tool_unknown_vault_errors(repo: Path):
  reg = VaultRegistry({"v": VaultFS(root=repo)})

  result = await check_git_lock.run(
    arguments={"vault": "ghost"}, bearer="tok", vault_registry=reg,
  )

  assert result["isError"] is True
