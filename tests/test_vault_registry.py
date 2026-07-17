"""CW-MCP-multi-vault-create-dir — VaultRegistry tests.

Covers env parsing (both formats + fallback + default), lookup by name,
default-to-first, and fail-loud on duplicate name.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import (
  DuplicateVaultNameError,
  LastVaultRemovalError,
  VaultNotFoundError,
  VaultRegistry,
  VaultRegistryError,
)


@pytest.fixture
def two_vaults(tmp_path: Path) -> tuple[Path, Path]:
  a = tmp_path / "alpha"
  b = tmp_path / "beta"
  a.mkdir()
  b.mkdir()
  return a, b


def test_parses_forge_vaults_env(two_vaults: tuple[Path, Path]):
  """Drain §5 test #1: canonical format returns expected registry."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"alpha:{a};beta:{b}"}
  reg = VaultRegistry.from_env(env)
  assert reg.names() == ["alpha", "beta"]
  assert reg.get("alpha").root == a.resolve()
  assert reg.get("beta").root == b.resolve()


def test_forge_vaults_tolerates_whitespace(two_vaults: tuple[Path, Path]):
  """Extra whitespace around names/paths and trailing `;` are tolerated."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"  alpha : {a} ;  beta:{b} ;"}
  reg = VaultRegistry.from_env(env)
  assert reg.names() == ["alpha", "beta"]


def test_falls_back_to_forge_vault_path(tmp_path: Path):
  """Drain §5 test #2: legacy single-vault env is honored when
  FORGE_VAULTS is unset. Registered under the name 'default'."""
  env = {"FORGE_VAULT_PATH": str(tmp_path)}
  reg = VaultRegistry.from_env(env)
  assert reg.names() == ["default"]
  assert reg.get("default").root == tmp_path.resolve()
  # `None` also returns the default (first-registered convention).
  assert reg.get(None).root == tmp_path.resolve()


def test_default_when_neither_set(tmp_path: Path, monkeypatch):
  """Drain §5 test #3: hardcoded default when neither env is set.

  Point HOME at a tmp_path so the ~/forge-vaults/bluh default exists
  for this test's construction.
  """
  monkeypatch.setenv("HOME", str(tmp_path))
  (tmp_path / "forge-vaults" / "bluh").mkdir(parents=True)
  reg = VaultRegistry.from_env({})
  assert reg.names() == ["default"]
  assert reg.get().root == (tmp_path / "forge-vaults" / "bluh").resolve()


def test_duplicate_name_raises(two_vaults: tuple[Path, Path]):
  """Drain §5 test #4: fail-loud on collision."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"same:{a};same:{b}"}
  with pytest.raises(DuplicateVaultNameError):
    VaultRegistry.from_env(env)


def test_get_by_name_returns_correct_vaultfs(two_vaults: tuple[Path, Path]):
  """Drain §5 test #5."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"alpha:{a};beta:{b}"}
  reg = VaultRegistry.from_env(env)
  assert reg.get("beta").root == b.resolve()


def test_get_without_name_returns_first(two_vaults: tuple[Path, Path]):
  """Drain §5 test #6: `None` (or empty string) returns the
  first-registered vault. Insertion order matters."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"beta:{b};alpha:{a}"}
  reg = VaultRegistry.from_env(env)
  assert reg.get(None).root == b.resolve()
  assert reg.get("").root == b.resolve()


def test_get_unknown_name_raises(two_vaults: tuple[Path, Path]):
  """Unknown vault name surfaces as VaultNotFoundError with the
  available vault list so the agent can auto-correct."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"alpha:{a};beta:{b}"}
  reg = VaultRegistry.from_env(env)
  with pytest.raises(VaultNotFoundError) as excinfo:
    reg.get("gamma")
  assert "alpha" in str(excinfo.value)
  assert "beta" in str(excinfo.value)


def test_malformed_entry_missing_colon_raises(tmp_path: Path):
  env = {"FORGE_VAULTS": f"no_colon_here_{tmp_path}"}
  with pytest.raises(VaultRegistryError):
    VaultRegistry.from_env(env)


def test_list_returns_all_registered(two_vaults: tuple[Path, Path]):
  """VaultRegistry.list is what forge_list_vaults returns."""
  a, b = two_vaults
  env = {"FORGE_VAULTS": f"alpha:{a};beta:{b}"}
  reg = VaultRegistry.from_env(env)
  entries = reg.list()
  assert len(entries) == 2
  names = [e["name"] for e in entries]
  assert names == ["alpha", "beta"]
  # All entries have the expected shape.
  for e in entries:
    assert set(e.keys()) == {"name", "path", "note_count"}
    assert e["note_count"] == 0  # empty vaults


# ---------------------------------------------------------------------------
# Runtime add / remove (CW-MCP-runtime-vault-registration)
# ---------------------------------------------------------------------------


def test_add_updates_list_output(two_vaults: tuple[Path, Path], tmp_path: Path):
  """Drain §5 test #10 — after add(), list() reflects it."""
  a, b = two_vaults
  reg = VaultRegistry({"alpha": VaultFS(root=a)})
  new = tmp_path / "runtime_added"
  new.mkdir()
  reg.add("runtime_added", VaultFS(root=new))
  entries = reg.list()
  assert [e["name"] for e in entries] == ["alpha", "runtime_added"]


def test_remove_updates_list_output(two_vaults: tuple[Path, Path]):
  """Drain §5 test #11 — after remove(), list() no longer shows it."""
  a, b = two_vaults
  reg = VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)})
  reg.remove("beta")
  entries = reg.list()
  assert [e["name"] for e in entries] == ["alpha"]


def test_add_rejects_duplicate_name(two_vaults: tuple[Path, Path], tmp_path: Path):
  """Adding a name that's already in the registry raises."""
  a, _ = two_vaults
  reg = VaultRegistry({"alpha": VaultFS(root=a)})
  other = tmp_path / "other"
  other.mkdir()
  with pytest.raises(DuplicateVaultNameError):
    reg.add("alpha", VaultFS(root=other))


def test_remove_rejects_unknown_name(two_vaults: tuple[Path, Path]):
  a, b = two_vaults
  reg = VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)})
  with pytest.raises(VaultNotFoundError):
    reg.remove("gamma")


def test_remove_last_vault_raises(two_vaults: tuple[Path, Path]):
  """Safety invariant: cannot leave the registry empty."""
  a, _ = two_vaults
  reg = VaultRegistry({"alpha": VaultFS(root=a)})
  with pytest.raises(LastVaultRemovalError):
    reg.remove("alpha")
  # Vault still there after refusal.
  assert reg.names() == ["alpha"]
