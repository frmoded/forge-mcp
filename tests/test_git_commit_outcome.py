"""Drain 2026-08-16-1810 — `_git_commit_file` must say WHICH thing went wrong.

Drain 1710 walked into this wall. Wizard's murmuration commit came back with
`git_sha: null` on a git-tracked vault, and the investigation could not tell
whether the write had been absorbed by another actor's commit (that day had
`776a495`, `c2192d3`, and Obsidian restamps all touching the same file) or
whether git had genuinely failed. The response carried nothing, the logs
carried nothing, and by the time anyone looked the evidence was gone.

Both outcomes are a bare `git_sha: None` today, which is exactly what made a
five-minute question into an archaeology expedition. These are the tests that
report said the fix must include.

Absorption is NOT an error (§8) — it is a documented, legitimate outcome. The
fix names it; it does not reject it.

Real temp git repos throughout: "did git commit anything" is not a question a
mock can answer honestly.
"""
import subprocess
from pathlib import Path

import pytest

from forge_mcp.vault_fs import _git_commit_file


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


def test_a_normal_commit_reports_committed(repo: Path):
  """Regression guard — the ordinary path must be untouched."""
  path = repo / "note.md"
  path.write_text("changed by the tool\n")

  result = _git_commit_file(repo, path, "tool: edit note")

  assert result.outcome == "committed"
  assert result.git_sha is not None
  assert result.committed is True
  assert result.error_summary is None


def test_an_absorbed_write_reports_nothing_to_commit_and_is_NOT_an_error(repo: Path):
  """THE case drain 1710 could not distinguish.

  Another actor commits the tool's write before the tool gets to it. There is
  then nothing staged, `git commit` exits non-zero, and today that is
  indistinguishable from git being broken.
  """
  path = repo / "note.md"
  path.write_text("written by the tool\n")
  # ... and absorbed by somebody else's commit before we run:
  _git(repo, "add", "-A")
  _git(repo, "commit", "-q", "-m", "another actor's commit")

  result = _git_commit_file(repo, path, "tool: edit note")

  assert result.outcome == "nothing-to-commit"
  assert result.git_sha is None
  assert result.committed is False
  # §8 — absorption is legitimate. It must not be dressed up as a failure.
  assert result.error_summary is None


def test_a_real_git_failure_reports_git_error_with_a_summary(repo: Path):
  """A held index.lock is the collision the 1710 timeline made plausible."""
  path = repo / "note.md"
  path.write_text("changed\n")
  lock = repo / ".git" / "index.lock"
  lock.write_text("")  # git refuses to touch the index while this exists
  try:
    result = _git_commit_file(repo, path, "tool: edit note")
  finally:
    lock.unlink(missing_ok=True)

  assert result.outcome == "git-error"
  assert result.git_sha is None
  assert result.committed is False
  assert result.error_summary, "a git error must carry enough to diagnose it"


def test_the_three_outcomes_are_actually_distinct(repo: Path):
  """Non-vacuity: the whole point is that these do not collapse."""
  outcomes = set()

  path = repo / "note.md"
  path.write_text("one\n")
  outcomes.add(_git_commit_file(repo, path, "m1").outcome)

  path.write_text("two\n")
  _git(repo, "add", "-A")
  _git(repo, "commit", "-q", "-m", "absorbed")
  outcomes.add(_git_commit_file(repo, path, "m2").outcome)

  path.write_text("three\n")
  lock = repo / ".git" / "index.lock"
  lock.write_text("")
  try:
    outcomes.add(_git_commit_file(repo, path, "m3").outcome)
  finally:
    lock.unlink(missing_ok=True)

  assert outcomes == {"committed", "nothing-to-commit", "git-error"}


def test_error_summary_is_truncated_not_a_full_dump(repo: Path):
  """§4 — enough to diagnose, not the whole of git's stderr."""
  path = repo / "note.md"
  path.write_text("changed\n")
  lock = repo / ".git" / "index.lock"
  lock.write_text("")
  try:
    result = _git_commit_file(repo, path, "m")
  finally:
    lock.unlink(missing_ok=True)

  assert result.error_summary is not None
  assert len(result.error_summary) <= 400


def test_commit_recipe_surfaces_git_outcome():
  """§3 — the distinction has to reach the caller, or it may as well not
  exist. This is what wizard's HARD RULE 3b readback quotes when escalating
  a null git_sha."""
  from forge_mcp.tools import commit_recipe

  assert "git_outcome" in commit_recipe.OUTPUT_SCHEMA["properties"], (
    "commit_recipe must surface which git outcome occurred"
  )
