"""forge_rename_note must VERIFY its post-condition before it reports success (drain 2026-10-11-0900 §5) — the same contract
forge_delete_note got in drain 2026-10-10-2100: old path absent and new path present in the working tree, the index and HEAD; lock
errors retried with a bounded backoff; anything else an ERROR.

The rename shares `_git_aware_move` with `move_asset`, so both are covered (one test below pins that).
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import vault_fs as vault_fs_module
from forge_mcp.tools import create_note, rename_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

LOCK_STDERR = (
  "fatal: Unable to create '/vault/.git/index.lock': File exists.\n\n"
  "Another git process seems to be running in this repository."
)
_REAL_RUN = subprocess.run


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


async def _seed(reg: VaultRegistry, *note_ids: str) -> None:
  for nid in note_ids:
    await create_note.run(arguments={"note_id": nid, "description": "body of " + nid}, bearer="tok", vault_registry=reg)
  root = reg.get().root
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "seed")


async def _rename(reg: VaultRegistry, old: str, new: str) -> dict[str, Any]:
  return await rename_note.run(arguments={"old_note_id": old, "new_note_id": new}, bearer="tok", vault_registry=reg)


def _assert_really_moved(root: Path, old: str, new: str) -> None:
  assert not (root / old).exists(), f"{old} is still on disk"
  assert (root / new).is_file(), f"{new} is not on disk"
  assert _REAL_RUN(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", old], capture_output=True, text=True).returncode != 0, "old still in the index"
  assert _REAL_RUN(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", new], capture_output=True, text=True).returncode == 0, "new not in the index"
  assert _git(root, "ls-tree", "HEAD", "--", old).strip() == "", "old still in HEAD"
  assert _git(root, "ls-tree", "HEAD", "--", new).strip() != "", "new not in HEAD"
  assert _git(root, "status", "--porcelain", "--", old, new) == "", "git status still reports an entry"


class _Injector:
  def __init__(self) -> None:
    self.calls: list[list[str]] = []
    self.before: dict[str, Any] = {}
    self.after: dict[str, Any] = {}
    self.fail: dict[str, int] = {}
    self.noop: set[str] = set()

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
    hook = self.before.get(sub)
    if hook is not None:
      hook(list(args))
    remaining = self.fail.get(sub, 0)
    if remaining != 0:
      if remaining > 0:
        self.fail[sub] = remaining - 1
      raise subprocess.CalledProcessError(128, args, output="", stderr=LOCK_STDERR)
    if sub in self.noop:
      return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    out = _REAL_RUN(args, *a, **kw)
    hook_after = self.after.get(sub)
    if hook_after is not None:
      hook_after(list(args))
    return out

  def count(self, sub: str) -> int:
    return sum(1 for c in self.calls if self.sub(c) == sub)


@pytest.fixture
def inject(monkeypatch: pytest.MonkeyPatch) -> _Injector:
  inj = _Injector()
  monkeypatch.setattr(vault_fs_module.subprocess, "run", inj)
  return inj


# ---- lock errors ------------------------------------------------------------------------------------------------------

async def test_lock_failure_on_git_mv_is_retried_then_succeeds(registry: VaultRegistry, inject: _Injector, no_real_sleep: list[float]):
  await _seed(registry, "old_name")
  inject.fail["mv"] = 2
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is False, result["content"][0]["text"]
  _assert_really_moved(registry.get().root, "old_name.md", "new_name.md")
  assert inject.count("mv") == 3
  assert len(no_real_sleep) == 2 and all(0 < s <= 0.5 for s in no_real_sleep)
  assert result["structuredContent"]["git_sha"] == _git(registry.get().root, "rev-parse", "HEAD").strip()


async def test_lock_failure_on_commit_is_retried_not_swallowed(registry: VaultRegistry, inject: _Injector):
  """Before: `_git_commit_paths` returned None, the tool said "Renamed" with git_sha null and left the move only staged."""
  await _seed(registry, "old_name")
  inject.fail["commit"] = 1
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is False, result["content"][0]["text"]
  assert isinstance(result["structuredContent"]["git_sha"], str)
  _assert_really_moved(registry.get().root, "old_name.md", "new_name.md")


async def test_persistent_lock_failure_on_git_mv_is_an_error_not_success(registry: VaultRegistry, inject: _Injector, no_real_sleep: list[float]):
  await _seed(registry, "old_name")
  inject.fail["mv"] = -1
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is True
  assert "lock" in result["content"][0]["text"].lower()
  assert len(no_real_sleep) <= 8
  assert (registry.get().root / "old_name.md").exists() and not (registry.get().root / "new_name.md").exists()


async def test_persistent_commit_failure_is_an_error_that_says_the_move_is_only_staged(registry: VaultRegistry, inject: _Injector):
  await _seed(registry, "old_name")
  inject.fail["commit"] = -1
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is True
  text = result["content"][0]["text"].lower()
  assert "staged but not committed" in text, text     # the commit's own failure is surfaced, not folded into a generic retry message
  assert result["structuredContent"]["git_sha"] is None


# ---- the old path comes back ------------------------------------------------------------------------------------------

async def test_old_path_recreated_right_after_git_mv_never_lands_as_a_commit(registry: VaultRegistry, inject: _Injector):
  """A writer re-creates the OLD path between `git mv` and the commit. A path-scoped commit that names the old path reads the working
  tree for it, so it would record the resurrected file (as an added/modified old note). It must be taken out first."""
  await _seed(registry, "old_name")
  root = registry.get().root
  fired = {"n": 0}

  def resurrect(_args: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "old_name.md").write_text("---\nsource_facet: python\n---\n\nwritten by someone else\n", encoding="utf-8")

  inject.after["mv"] = resurrect
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is False, result["content"][0]["text"]
  _assert_really_moved(root, "old_name.md", "new_name.md")
  log = _git(root, "log", "--format=%H", "--name-status", "--", "old_name.md")
  statuses = [ln.split("\t")[0] for ln in log.splitlines() if "\t" in ln]
  assert statuses == ["D", "A"], f"old_name.md history must be seed(A) then the move(D) only, got {statuses}"


async def test_old_path_recreated_before_the_commit_is_detected_and_retried(registry: VaultRegistry, inject: _Injector):
  await _seed(registry, "old_name")
  root = registry.get().root
  fired = {"n": 0}

  def resurrect(_args: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "old_name.md").write_text("back\n", encoding="utf-8")

  inject.before["commit"] = resurrect      # after the guard's own check: the commit itself records the resurrection
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is False, result["content"][0]["text"]
  _assert_really_moved(root, "old_name.md", "new_name.md")


async def test_persistent_resurrection_of_the_old_path_is_an_error_never_a_false_success(registry: VaultRegistry, inject: _Injector):
  await _seed(registry, "old_name")
  root = registry.get().root
  n = {"v": 0}

  def resurrect(_args: list[str]) -> None:
    n["v"] += 1
    (root / "old_name.md").write_text(f"back again {n['v']}\n", encoding="utf-8")

  inject.before["commit"] = resurrect
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "did not take effect" in text and "old_name" in text, text
  assert inject.count("commit") <= 6


async def test_success_is_not_reported_when_every_git_step_exits_zero_but_nothing_moved(registry: VaultRegistry, inject: _Injector):
  await _seed(registry, "old_name")
  inject.noop |= {"mv", "commit"}
  result = await _rename(registry, "old_name", "new_name")
  assert result["isError"] is True
  assert not (registry.get().root / "new_name.md").exists()
  assert (registry.get().root / "old_name.md").exists(), "when the move did not land, the old file is the ONLY copy and must be left alone"


async def test_untracked_vault_rename_that_leaves_the_old_file_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
  root = tmp_path / "plain"
  root.mkdir()
  reg = VaultRegistry({"default": VaultFS(root=root)})
  await create_note.run(arguments={"note_id": "old_name", "description": "x"}, bearer="tok", vault_registry=reg)
  real_rename = Path.rename

  def fake_rename(self, target):          # claims success but copies instead of moving
    Path(target).write_bytes(self.read_bytes())
    return Path(target)

  monkeypatch.setattr(Path, "rename", fake_rename)
  result = await _rename(reg, "old_name", "new_name")
  monkeypatch.setattr(Path, "rename", real_rename)
  assert result["isError"] is True
  assert "still" in result["content"][0]["text"].lower()


# ---- move_asset shares the helper -------------------------------------------------------------------------------------

async def test_move_asset_gets_the_same_post_condition(registry: VaultRegistry, inject: _Injector):
  root = registry.get().root
  (root / "img").mkdir()
  (root / "img" / "a.svg").write_text("<svg/>", encoding="utf-8")
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "seed")
  inject.fail["commit"] = 1
  vault = registry.get()
  new_path, sha, msg = vault.move_asset("img/a.svg", "img/b.svg")
  assert isinstance(sha, str) and len(sha) == 40
  _assert_really_moved(root, "img/a.svg", "img/b.svg")


# ---- serialization and the contract ------------------------------------------------------------------------------------

async def test_renames_and_deletes_in_one_vault_share_one_lock(tmp_path: Path):
  a = tmp_path / "a"
  a.mkdir()
  assert vault_fs_module._vault_git_write_lock(a) is vault_fs_module._vault_delete_lock(a)


async def test_concurrent_renames_never_overlap_their_git_sequences(registry: VaultRegistry, monkeypatch: pytest.MonkeyPatch):
  import time as _time

  ids = [f"n{i}" for i in range(5)]
  await _seed(registry, *ids)
  vault = registry.get()
  state = {"in_flight": 0, "max": 0}
  guard = threading.Lock()

  def counting_run(args, *a, **kw):
    with guard:
      state["in_flight"] += 1
      state["max"] = max(state["max"], state["in_flight"])
    try:
      _time.sleep(0.01)
      return _REAL_RUN(args, *a, **kw)
    finally:
      with guard:
        state["in_flight"] -= 1

  monkeypatch.setattr(vault_fs_module.subprocess, "run", counting_run)
  errors: list[BaseException] = []

  def work(nid: str) -> None:
    try:
      vault.rename_note(nid, nid + "_renamed")
    except BaseException as exc:  # noqa: BLE001
      errors.append(exc)

  threads = [threading.Thread(target=work, args=(n,)) for n in ids]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  assert errors == []
  assert state["max"] == 1, f"{state['max']} git commands were in flight at once"
  for nid in ids:
    _assert_really_moved(vault.root, f"{nid}.md", f"{nid}_renamed.md")


async def test_clean_rename_keeps_its_contract(registry: VaultRegistry):
  await _seed(registry, "old_name", "bystander")
  result = await _rename(registry, "old_name", "sub/new_name")
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["git_tracked"] is True and isinstance(sc["git_sha"], str) and len(sc["git_sha"]) == 40
  assert sc["message"] == "rename note old_name → sub/new_name"
  _assert_really_moved(registry.get().root, "old_name.md", "sub/new_name.md")
  assert (registry.get().root / "bystander.md").exists()
