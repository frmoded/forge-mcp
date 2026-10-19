"""forge_delete_note on a DIRTY tracked note must not fail once and succeed on retry (drain 2026-10-19-0900 §2; CCQA 2026-10-18 step 11).

Observed once in 17 CCQA runs: "git rm for 'ccqa-scratch/regression_16th_hihat' failed: error: the following file has local modifications ...
(use --cached to keep the file, or -f to force removal)"; the identical retry succeeded. The tool's contract is checkout-HEAD then `git rm`, so
after a successful checkout the tree for that path is clean — the failure means something re-wrote the file BETWEEN the checkout and the rm
(the plugin's Run write-back, a second pane). These tests inject exactly that re-write and pin the recovery:

  * the checkout is VERIFIED (`git status --porcelain -- <rel>` empty), and retried once, bounded, when it did not hold;
  * if `git rm` still refuses with "local modifications", the path is checked out again, verified, and only THEN removed with `-f`;
  * `-f` is never used on a path whose checkout failed or whose verification did not come back clean;
  * a writer that never stops is an ERROR after the bound, not a success and not an endless loop.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import vault_fs as vault_fs_module
from forge_mcp.tools import create_note, delete_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

_REAL_RUN = subprocess.run
REWRITTEN = "---\nsource_facet: python\n---\n\nre-written by another writer after the checkout\n"


def _git(root: Path, *args: str) -> str:
  return _REAL_RUN(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_vault(tmp_path: Path) -> VaultFS:
  root = tmp_path / "gitvault"
  root.mkdir()
  _REAL_RUN(["git", "init", "-q"], cwd=root, check=True)
  _REAL_RUN(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
  _REAL_RUN(["git", "config", "user.name", "T"], cwd=root, check=True)
  return VaultFS(root=root)


@pytest.fixture
def registry(git_vault: VaultFS) -> VaultRegistry:
  return VaultRegistry({"default": git_vault})


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
  slept: list[float] = []
  monkeypatch.setattr(vault_fs_module, "_sleep", slept.append)
  return slept


class _Spy:
  """Wraps subprocess.run: records every git call and lets a test interfere before / after chosen subcommands."""

  def __init__(self) -> None:
    self.calls: list[list[str]] = []
    self.before: dict[str, Any] = {}
    self.after: dict[str, Any] = {}

  @staticmethod
  def sub(args: Any) -> str | None:
    if isinstance(args, list) and len(args) >= 4 and args[0] == "git" and args[1] == "-C":
      return args[3]
    return None

  def __call__(self, args, *a, **kw):
    sub = self.sub(args)
    if sub is None:
      return _REAL_RUN(args, *a, **kw)
    self.calls.append(list(args))
    if sub in self.before:
      self.before[sub](list(args))
    out = _REAL_RUN(args, *a, **kw)
    if sub in self.after:
      self.after[sub](list(args))
    return out

  def subs(self) -> list[str]:
    return [s for s in (self.sub(c) for c in self.calls) if s]

  def rm_calls(self) -> list[list[str]]:
    return [c for c in self.calls if self.sub(c) == "rm"]


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
  s = _Spy()
  monkeypatch.setattr(vault_fs_module.subprocess, "run", s)
  return s


async def _seed_dirty(reg: VaultRegistry, note_id: str = "hihat") -> Path:
  await create_note.run(arguments={"note_id": note_id, "description": "body"}, bearer="tok", vault_registry=reg)
  root = reg.get().root
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "seed")
  path = root / f"{note_id}.md"
  path.write_text(path.read_text() + "\nplugin re-derived a facet\n", encoding="utf-8")   # the dirty tracked note
  return path


async def _delete(reg: VaultRegistry, note_id: str = "hihat") -> dict[str, Any]:
  return await delete_note.run(arguments={"note_id": note_id}, bearer="tok", vault_registry=reg)


def _really_gone(root: Path, rel: str) -> None:
  assert not (root / rel).exists()
  assert _git(root, "ls-tree", "HEAD", "--", rel).strip() == ""
  assert _git(root, "status", "--porcelain", "--", rel) == ""


async def test_dirty_note_plain_delete_is_unchanged_and_never_forces(registry: VaultRegistry, spy: _Spy):
  path = await _seed_dirty(registry)
  result = await _delete(registry)
  assert result["isError"] is False, result["content"][0]["text"]
  _really_gone(registry.get().root, "hihat.md")
  assert all("-f" not in c for c in spy.rm_calls()), "a clean sequence must not force anything"
  assert len(spy.rm_calls()) == 1


async def test_rewrite_between_checkout_and_the_verification_is_caught_and_the_checkout_retried(registry: VaultRegistry, spy: _Spy):
  path = await _seed_dirty(registry)
  fired = {"n": 0}

  def rewrite(_a: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      path.write_text(REWRITTEN, encoding="utf-8")

  spy.after["checkout"] = rewrite
  result = await _delete(registry)
  assert result["isError"] is False, result["content"][0]["text"]
  _really_gone(registry.get().root, "hihat.md")
  assert spy.subs().count("checkout") == 2, "the checkout was repeated once"
  assert all("-f" not in c for c in spy.rm_calls()), "verification caught it, so no force was needed"


async def test_the_incident_rewrite_between_verification_and_git_rm_recovers_with_a_second_checkout_then_force(registry: VaultRegistry, spy: _Spy):
  """CCQA step 11: `git rm` refused with 'local modifications'. The first attempt must not fail."""
  path = await _seed_dirty(registry)
  fired = {"n": 0}

  def rewrite(args: list[str]) -> None:
    if fired["n"] == 0 and "-f" not in args:
      fired["n"] = 1
      path.write_text(REWRITTEN, encoding="utf-8")

  spy.before["rm"] = rewrite
  result = await _delete(registry)
  assert result["isError"] is False, result["content"][0]["text"]
  _really_gone(registry.get().root, "hihat.md")
  rms = spy.rm_calls()
  assert len(rms) == 2 and "-f" not in rms[0] and "-f" in rms[1], rms
  # `-f` came only AFTER a second checkout of that same path, itself followed by a clean verification
  seq = spy.subs()
  last_force = max(i for i, c in enumerate(spy.calls) if spy.sub(c) == "rm" and "-f" in c)
  prior = seq[:last_force]
  assert prior[-2:] == ["checkout", "status"], prior


async def test_a_writer_that_never_stops_is_an_error_after_a_bounded_number_of_tries_and_never_forces(registry: VaultRegistry, spy: _Spy):
  path = await _seed_dirty(registry)
  spy.after["checkout"] = lambda _a: path.write_text(REWRITTEN, encoding="utf-8")
  result = await _delete(registry)
  assert result["isError"] is True
  assert spy.subs().count("checkout") <= 6, "bounded"
  assert all("-f" not in c for c in spy.rm_calls()), "never -f on a path whose checkout did not verify clean"
  assert path.exists(), "nothing was destroyed on an error"


async def test_a_failed_checkout_never_leads_to_rm_or_force(registry: VaultRegistry, spy: _Spy):
  await _seed_dirty(registry)

  def fail(_a: list[str]) -> None:
    raise subprocess.CalledProcessError(1, _a, output="", stderr="error: pathspec 'hihat.md' did not match any file(s) known to git")

  spy.before["checkout"] = fail
  result = await _delete(registry)
  assert result["isError"] is True
  assert spy.rm_calls() == []


async def test_the_verification_is_a_real_gate_a_note_that_stays_dirty_after_checkout_is_not_removed(registry: VaultRegistry, spy: _Spy, monkeypatch: pytest.MonkeyPatch):
  """Non-vacuity partner: if the verification said 'clean' regardless, the incident test above could pass for the wrong reason."""
  path = await _seed_dirty(registry)
  spy.after["checkout"] = lambda _a: path.write_text(REWRITTEN, encoding="utf-8")
  before = len(spy.calls)
  result = await _delete(registry)
  assert result["isError"] is True
  assert "status" in spy.subs()[before:], "the tree was actually inspected"


async def test_a_git_rm_failure_that_is_not_local_modifications_is_never_forced(registry: VaultRegistry, spy: _Spy):
  """The force path is for ONE diagnosis only. An unrelated `git rm` failure stays an error and `-f` is never tried."""
  await _seed_dirty(registry)

  def fail_rm(args: list[str]) -> None:
    raise subprocess.CalledProcessError(128, args, output="", stderr="fatal: pathspec 'hihat.md' is beyond a symbolic link")

  spy.before["rm"] = fail_rm
  result = await _delete(registry)
  assert result["isError"] is True
  assert all("-f" not in c for c in spy.rm_calls())
  assert spy.subs().count("checkout") == 1, "no second reset for a diagnosis we do not recognise"
