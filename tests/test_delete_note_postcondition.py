"""forge_delete_note must VERIFY its post-condition before it reports success
(drain 2026-10-10-2100 (c); CCQA findings F1/F2, test-reports/2026-10-10-1325).

Observed: CCQA issued 7 deletes in one parallel batch; all reported success and
all committed at the same second, but commit 7df5c08 in ClaudeQA, labelled
"delete note ccqa-scratch/regression_16th_hihat", has stat `1 insertion(+),
3 deletions(-)`: it MODIFIED the file, which stayed on disk, tracked in HEAD,
clean in `git status`.

What 7df5c08 is: the file's own frontmatter rewritten (`source_facet:
description` -> `python`, two `python_derived_*` lines dropped) — content that
is not HEAD's, so a process OTHER than this tool wrote the path in the window
between `git rm` and the path-scoped `git commit -- <rel>`. `git commit --only`
reads the WORKING TREE for its pathspec, so a re-created file is committed as a
modification and the "deletion" is undone. The tool then saw exit code 0 and
returned success. Separately, `_git_commit_paths` swallowed every commit failure
(index.lock included) and the tool still said "Deleted".

These tests pin the new contract: success is reported only when the path is
absent from the working tree, the index, and the new HEAD; lock errors are
retried with a short bounded backoff; anything else is an ERROR.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from forge_mcp import vault_fs as vault_fs_module
from forge_mcp.tools import create_note, delete_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

LOCK_STDERR = (
  "fatal: Unable to create '/vault/.git/index.lock': File exists.\n\n"
  "Another git process seems to be running in this repository."
)

_REAL_RUN = subprocess.run


def _git(root: Path, *args: str) -> str:
  return _REAL_RUN(
    ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
  ).stdout


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
  """Record backoffs instead of sleeping; also lets a test assert the bound."""
  slept: list[float] = []
  monkeypatch.setattr(vault_fs_module, "_sleep", slept.append)
  return slept


async def _seed(reg: VaultRegistry, *note_ids: str) -> None:
  for nid in note_ids:
    await create_note.run(
      arguments={"note_id": nid, "description": "body of " + nid},
      bearer="tok", vault_registry=reg,
    )
  root = reg.get().root
  _git(root, "add", "-A")
  _git(root, "commit", "-q", "-m", "seed")


async def _delete(reg: VaultRegistry, note_id: str) -> dict[str, Any]:
  return await delete_note.run(
    arguments={"note_id": note_id}, bearer="tok", vault_registry=reg,
  )


def _assert_really_gone(root: Path, rel: str) -> None:
  assert not (root / rel).exists(), f"{rel} is still on disk"
  tracked = _REAL_RUN(
    ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
    capture_output=True, text=True,
  )
  assert tracked.returncode != 0, f"{rel} is still in the index"
  assert _git(root, "ls-tree", "HEAD", "--", rel).strip() == "", f"{rel} is still in HEAD"
  assert _git(root, "status", "--porcelain", "--", rel) == "", "an entry remains in git status"


class _Injector:
  """Wraps subprocess.run: lets a test fail / observe / interfere with chosen git subcommands."""

  def __init__(self) -> None:
    self.calls: list[list[str]] = []
    self.before: dict[str, Any] = {}   # subcommand -> callable(args) run before the real call
    self.after: dict[str, Any] = {}    # subcommand -> callable(args) run after the real call succeeded
    self.fail: dict[str, int] = {}     # subcommand -> remaining forced lock failures (-1 = forever)
    self.noop: set[str] = set()        # subcommands that "succeed" without doing anything

  @staticmethod
  def sub(args: list[str]) -> str | None:
    if len(args) >= 4 and args[0] == "git" and args[1] == "-C":
      return args[3]
    return None

  def __call__(self, args, *a, **kw):
    sub = self.sub(list(args)) if isinstance(args, list) else None
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


# ---- lock failures: retried with a bounded backoff, then success --------------------------------------------------------

async def test_lock_failure_on_git_rm_is_retried_then_succeeds(
  registry: VaultRegistry, inject: _Injector, no_real_sleep: list[float],
):
  await _seed(registry, "victim")
  inject.fail["rm"] = 2
  result = await _delete(registry, "victim")
  assert result["isError"] is False, result["content"][0]["text"]
  _assert_really_gone(registry.get().root, "victim.md")
  assert inject.count("rm") == 3, "two forced lock failures, then the real rm"
  assert len(no_real_sleep) == 2 and all(0 < s <= 0.5 for s in no_real_sleep), no_real_sleep
  assert result["structuredContent"]["git_sha"] == _git(registry.get().root, "rev-parse", "HEAD").strip()


async def test_lock_failure_on_commit_is_retried_not_swallowed(
  registry: VaultRegistry, inject: _Injector,
):
  """Before: `_git_commit_paths` returned None, the tool said "Deleted" with git_sha null and left `D  <rel>` staged."""
  await _seed(registry, "victim")
  inject.fail["commit"] = 1
  result = await _delete(registry, "victim")
  assert result["isError"] is False, result["content"][0]["text"]
  assert isinstance(result["structuredContent"]["git_sha"], str)
  _assert_really_gone(registry.get().root, "victim.md")


async def test_persistent_lock_failure_on_git_rm_is_an_error_not_success(
  registry: VaultRegistry, inject: _Injector, no_real_sleep: list[float],
):
  await _seed(registry, "victim")
  inject.fail["rm"] = -1
  result = await _delete(registry, "victim")
  assert result["isError"] is True
  assert "index.lock" in result["content"][0]["text"] or "lock" in result["content"][0]["text"].lower()
  assert len(no_real_sleep) <= 8, "the backoff is bounded"
  assert (registry.get().root / "victim.md").exists(), "nothing was claimed deleted, and the file is intact"


async def test_persistent_commit_failure_is_an_error_that_says_the_deletion_is_only_staged(
  registry: VaultRegistry, inject: _Injector,
):
  await _seed(registry, "victim")
  inject.fail["commit"] = -1
  result = await _delete(registry, "victim")
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "not committed" in text.lower() or "staged" in text.lower(), text
  assert result["structuredContent"]["git_sha"] is None


# ---- the 7df5c08 shape: re-created between `git rm` and the commit ---------------------------------------------------------

async def test_file_recreated_before_the_commit_is_detected_and_the_delete_retried(
  registry: VaultRegistry, inject: _Injector,
):
  """An external writer puts the file back right before the path-scoped commit (the commit then records a MODIFICATION)."""
  await _seed(registry, "victim")
  root = registry.get().root
  fired = {"n": 0}

  def resurrect(_args: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "victim.md").write_text("---\nsource_facet: python\n---\n\nrewritten by someone else\n", encoding="utf-8")

  inject.before["commit"] = resurrect
  result = await _delete(registry, "victim")
  assert result["isError"] is False, result["content"][0]["text"]
  _assert_really_gone(root, "victim.md")
  assert inject.count("commit") >= 1


async def test_file_recreated_right_after_git_rm_never_lands_as_a_modification_commit(
  registry: VaultRegistry, inject: _Injector,
):
  """The writer strikes between `git rm` and the commit. The tool must take the file out again BEFORE committing, so history
  never records a modification of the note — only its seed and its deletion."""
  await _seed(registry, "victim")
  root = registry.get().root
  fired = {"n": 0}

  def resurrect(_args: list[str]) -> None:
    if fired["n"] == 0:
      fired["n"] = 1
      (root / "victim.md").write_text("rewritten by someone else\n", encoding="utf-8")

  inject.after["rm"] = resurrect
  result = await _delete(registry, "victim")
  assert result["isError"] is False, result["content"][0]["text"]
  _assert_really_gone(root, "victim.md")
  log = _git(root, "log", "--format=%H", "--name-status", "--", "victim.md")
  statuses = [ln.split("\t")[0] for ln in log.splitlines() if "\t" in ln]
  assert statuses == ["D", "A"], f"history of victim.md must be seed(A) then delete(D) only, got {statuses}"


async def test_persistent_resurrection_is_an_error_never_a_false_success(
  registry: VaultRegistry, inject: _Injector, no_real_sleep: list[float],
):
  await _seed(registry, "victim")
  root = registry.get().root

  writes = {"n": 0}

  def resurrect(_args: list[str]) -> None:
    # Different bytes every time, so each commit is a real MODIFICATION (the 7df5c08 shape), not a no-op.
    writes["n"] += 1
    (root / "victim.md").write_text(f"back again {writes['n']}\n", encoding="utf-8")

  inject.before["commit"] = resurrect
  result = await _delete(registry, "victim")
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "did not take effect" in text and "still" in text.lower(), text
  assert inject.count("commit") == 3, "bounded: exactly _DELETE_ATTEMPTS sequences"


async def test_resurrection_with_identical_bytes_is_still_an_error_not_a_success(
  registry: VaultRegistry, inject: _Injector,
):
  """Same bytes as HEAD: the second commit finds nothing to commit. Still an ERROR, never "Deleted"."""
  await _seed(registry, "victim")
  root = registry.get().root

  def resurrect(_args: list[str]) -> None:
    (root / "victim.md").write_text("back again\n", encoding="utf-8")

  inject.before["commit"] = resurrect
  result = await _delete(registry, "victim")
  assert result["isError"] is True


async def test_success_is_not_reported_when_every_git_step_exits_zero_but_nothing_was_removed(
  registry: VaultRegistry, inject: _Injector,
):
  """The post-condition is checked against the REPO, not inferred from exit codes."""
  await _seed(registry, "victim")
  inject.noop |= {"rm", "commit"}
  result = await _delete(registry, "victim")
  assert result["isError"] is True
  assert "still" in result["content"][0]["text"].lower()


async def test_untracked_vault_unlink_that_leaves_the_file_is_an_error(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
  root = tmp_path / "plain"
  root.mkdir()
  reg = VaultRegistry({"default": VaultFS(root=root)})
  await create_note.run(arguments={"note_id": "victim", "description": "x"}, bearer="tok", vault_registry=reg)
  monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: None)
  result = await _delete(reg, "victim")
  assert result["isError"] is True
  assert (root / "victim.md").exists()


# ---- serialization -------------------------------------------------------------------------------------------------------

def test_one_lock_per_vault_root_and_different_roots_do_not_share(tmp_path: Path):
  a, b = tmp_path / "a", tmp_path / "b"
  a.mkdir(); b.mkdir()
  assert vault_fs_module._vault_delete_lock(a) is vault_fs_module._vault_delete_lock(a)
  assert vault_fs_module._vault_delete_lock(a) is not vault_fs_module._vault_delete_lock(b)


async def test_seven_concurrent_deletes_in_one_vault_all_really_go(registry: VaultRegistry):
  """CCQA's batch: 7 deletes at once. Every one must be gone from disk, index and HEAD — and each must be its own delete commit."""
  ids = [f"scratch_{i}" for i in range(7)]
  await _seed(registry, *ids)
  vault = registry.get()
  before = int(_git(vault.root, "rev-list", "--count", "HEAD").strip())
  results: list[Any] = [None] * len(ids)

  def work(i: int) -> None:
    try:
      results[i] = vault.delete_note(ids[i])
    except Exception as exc:  # noqa: BLE001 — surfaced by the assertions below
      results[i] = exc

  threads = [threading.Thread(target=work, args=(i,)) for i in range(len(ids))]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  assert all(isinstance(r, tuple) for r in results), results
  for nid in ids:
    _assert_really_gone(vault.root, f"{nid}.md")
  assert int(_git(vault.root, "rev-list", "--count", "HEAD").strip()) == before + len(ids)
  assert _git(vault.root, "status", "--porcelain") == ""
  for r in results:
    assert isinstance(r[1], str) and len(r[1]) == 40, r


async def test_concurrent_deletes_never_overlap_their_git_sequences(
  registry: VaultRegistry, monkeypatch: pytest.MonkeyPatch,
):
  """Serialization is observable: at no moment are two delete sequences' git commands in flight at once."""
  import time as _time

  ids = [f"scratch_{i}" for i in range(6)]
  await _seed(registry, *ids)
  vault = registry.get()
  state = {"in_flight": 0, "max": 0}
  guard = threading.Lock()

  def counting_run(args, *a, **kw):
    with guard:
      state["in_flight"] += 1
      state["max"] = max(state["max"], state["in_flight"])
    try:
      _time.sleep(0.01)  # widen the window so an unserialized run overlaps
      return _REAL_RUN(args, *a, **kw)
    finally:
      with guard:
        state["in_flight"] -= 1

  monkeypatch.setattr(vault_fs_module.subprocess, "run", counting_run)
  errors: list[BaseException] = []

  def work(nid: str) -> None:
    try:
      vault.delete_note(nid)
    except BaseException as exc:  # noqa: BLE001
      errors.append(exc)

  threads = [threading.Thread(target=work, args=(n,)) for n in ids]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  assert errors == []
  assert state["max"] == 1, f"{state['max']} git commands were in flight at once"


# ---- the happy path keeps its contract ----------------------------------------------------------------------------------------

async def test_clean_delete_still_returns_a_sha_and_a_clean_tree(registry: VaultRegistry):
  await _seed(registry, "victim", "bystander")
  result = await _delete(registry, "victim")
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["git_tracked"] is True and isinstance(sc["git_sha"], str) and len(sc["git_sha"]) == 40
  assert sc["message"] == "delete note victim"
  _assert_really_gone(registry.get().root, "victim.md")
  assert (registry.get().root / "bystander.md").exists()
