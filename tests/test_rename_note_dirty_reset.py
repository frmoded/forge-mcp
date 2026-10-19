"""forge_rename_note / forge_move_asset must move the note AS IT IS IN HEAD, even when another writer re-writes it mid-call (drain 2026-10-19-1530).

Same family as forge_delete_note's dirty-first-call failure (drain 2026-10-19-0900), but the rename's failure is worse because it does not error: after
the pre-move `git checkout HEAD -- <old>`, anything that re-writes <old> before `git mv` is silently PROMOTED into the new path and committed
(`git mv` carries the working-tree content). These tests inject that re-write and pin the contract:

  * the checkout is VERIFIED (`git status --porcelain -- <old>` empty) and retried once, bounded;
  * after `git mv` the moved file must be byte-identical to HEAD's blob of <old>; if it is not, it is restored from the blob (once) before the commit;
  * a writer that never stops is an ERROR after the bound — never a commit of the writer's content, never a false success;
  * a failed checkout never leads to `git mv`.
(`git mv -f` would overwrite the DESTINATION, so unlike delete there is deliberately no force path.)
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import vault_fs as vault_fs_module
from forge_mcp.tools import create_note, rename_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

_REAL_RUN = subprocess.run
REWRITTEN = "---\nsource_facet: python\n---\n\nre-written by another writer\n"


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


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
  s = _Spy()
  monkeypatch.setattr(vault_fs_module.subprocess, "run", s)
  return s


async def _seed_dirty(reg: VaultRegistry) -> tuple[Path, str]:
  await create_note.run(arguments={"note_id": "old", "description": "the committed body"}, bearer="tok", vault_registry=reg)
  root = reg.get().root
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "seed")
  head_text = (root / "old.md").read_text()
  (root / "old.md").write_text(head_text + "\nplugin re-derived a facet\n")          # the dirty tracked source
  return root, head_text


async def _rename(reg: VaultRegistry) -> dict[str, Any]:
  return await rename_note.run(arguments={"old_note_id": "old", "new_note_id": "new"}, bearer="tok", vault_registry=reg)


def _committed(root: Path, rel: str) -> str:
  return _git(root, "show", f"HEAD:{rel}")


async def test_a_dirty_source_moves_as_HEAD_has_it(registry: VaultRegistry, spy: _Spy):
  root, head_text = await _seed_dirty(registry)
  result = await _rename(registry)
  assert result["isError"] is False, result["content"][0]["text"]
  assert (root / "new.md").read_text() == head_text
  assert _committed(root, "new.md") == head_text


async def test_a_rewrite_between_the_checkout_and_its_verification_is_caught_and_the_checkout_retried(registry: VaultRegistry, spy: _Spy):
  root, head_text = await _seed_dirty(registry)
  fired = {"n": 0}

  def rewrite(_a: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "old.md").write_text(REWRITTEN)

  spy.after["checkout"] = rewrite
  result = await _rename(registry)
  assert result["isError"] is False, result["content"][0]["text"]
  assert spy.subs().count("checkout") == 2
  assert _committed(root, "new.md") == head_text


async def test_THE_BUG_a_rewrite_between_verification_and_git_mv_is_not_promoted_into_the_new_path(registry: VaultRegistry, spy: _Spy):
  """git mv carries the working-tree content: without the post-move integrity check the writer's content is committed under the new name."""
  root, head_text = await _seed_dirty(registry)
  fired = {"n": 0}

  def rewrite(_a: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "old.md").write_text(REWRITTEN)

  spy.before["mv"] = rewrite
  result = await _rename(registry)
  assert result["isError"] is False, result["content"][0]["text"]
  assert _committed(root, "new.md") == head_text, "the commit holds HEAD's bytes, not the writer's"
  assert (root / "new.md").read_text() == head_text
  assert not (root / "old.md").exists()


async def test_a_writer_that_never_stops_is_an_error_and_its_content_is_never_committed(registry: VaultRegistry, spy: _Spy):
  root, head_text = await _seed_dirty(registry)
  spy.after["checkout"] = lambda _a: (root / "old.md").write_text(REWRITTEN)
  seed_sha = _git(root, "rev-parse", "HEAD").strip()
  result = await _rename(registry)
  assert result["isError"] is True
  assert spy.subs().count("checkout") <= 6, "bounded"
  assert "mv" not in spy.subs(), "never moved a path that would not verify clean"
  assert _git(root, "rev-parse", "HEAD").strip() == seed_sha, "nothing was committed"


async def test_a_failed_checkout_never_leads_to_git_mv(registry: VaultRegistry, spy: _Spy):
  await _seed_dirty(registry)

  def fail(args: list[str]) -> None:
    raise subprocess.CalledProcessError(1, args, output="", stderr="error: pathspec 'old.md' did not match any file(s) known to git")

  spy.before["checkout"] = fail
  result = await _rename(registry)
  assert result["isError"] is True
  assert "mv" not in spy.subs()


async def test_a_clean_rename_is_unchanged_one_checkout_one_mv(registry: VaultRegistry, spy: _Spy):
  await create_note.run(arguments={"note_id": "old", "description": "b"}, bearer="tok", vault_registry=registry)
  root = registry.get().root
  _git(root, "add", "-A"); _git(root, "commit", "-q", "-m", "seed")
  result = await _rename(registry)
  assert result["isError"] is False
  assert spy.subs().count("checkout") == 1 and spy.subs().count("mv") == 1
  assert _committed(root, "new.md").startswith("---")


async def test_the_integrity_check_is_a_real_gate_a_persistent_post_move_writer_is_an_error(registry: VaultRegistry, spy: _Spy):
  """Non-vacuity partner: if the post-move check were skipped, the BUG test above could pass for the wrong reason."""
  root, head_text = await _seed_dirty(registry)
  spy.after["mv"] = lambda _a: (root / "new.md").write_text(REWRITTEN)
  spy.after["add"] = lambda _a: (root / "new.md").write_text(REWRITTEN)      # and again after every restore: a writer that never stops
  seed_sha = _git(root, "rev-parse", "HEAD").strip()
  result = await _rename(registry)
  assert result["isError"] is True
  assert _git(root, "rev-parse", "HEAD").strip() == seed_sha, "the writer's content was never committed"


def test_move_asset_shares_the_same_guard(git_vault: VaultFS, monkeypatch: pytest.MonkeyPatch):
  root = git_vault.root
  (root / "pic.svg").write_text("<svg>committed</svg>")
  _git(root, "add", "-A"); _git(root, "commit", "-q", "-m", "seed")
  (root / "pic.svg").write_text("<svg>dirty</svg>")
  s = _Spy()
  monkeypatch.setattr(vault_fs_module.subprocess, "run", s)
  fired = {"n": 0}

  def rewrite(_a: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "pic.svg").write_text("<svg>rewritten</svg>")

  s.before["mv"] = rewrite
  git_vault.move_asset("pic.svg", "moved.svg")
  assert _git(root, "show", "HEAD:moved.svg") == "<svg>committed</svg>"
