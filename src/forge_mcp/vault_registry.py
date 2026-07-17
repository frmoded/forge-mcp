"""Multi-vault registry.

CW-MCP-multi-vault-create-dir (2026-07-16). Parses `FORGE_VAULTS` env
var into a dict of `name -> VaultFS`. Backwards-compat with legacy
`FORGE_VAULT_PATH` (single vault, name "default").

Env format:
    FORGE_VAULTS='bluh:/Users/foo/vaults/bluh;music:/Users/foo/projects/forge-music'

- Colon (`:`) separates vault name from vault path within an entry.
- Semicolon (`;`) separates entries.
- Whitespace-tolerant.
- Duplicate name raises VaultRegistryError at startup (fail loudly).
- Empty entries (trailing `;`) are ignored.

Fallback chain:
    1. FORGE_VAULTS set → parse it.
    2. FORGE_VAULT_PATH set → single-vault registry named "default".
    3. Neither set → single-vault registry named "default" at
       ~/forge-vaults/bluh.

Design (per prompt §Why): no server-side session state. Every tool
call passes `vault` explicitly or omits it (defaults to first-
configured). Agent's LLM context tracks "current vault" as
conversation memory.
"""
from __future__ import annotations

import os
from pathlib import Path

from .vault_fs import VaultFS, VaultFSError


class VaultRegistryError(Exception):
  """Base for registry errors."""


class VaultNotFoundError(VaultRegistryError):
  """No vault registered under the requested name."""


class DuplicateVaultNameError(VaultRegistryError):
  """FORGE_VAULTS contained two entries with the same name."""


_DEFAULT_VAULT_NAME = "default"
_DEFAULT_VAULT_PATH = "~/forge-vaults/bluh"


def _parse_forge_vaults_env(raw: str) -> dict[str, str]:
  """Parse `FORGE_VAULTS='name:path;name2:path2'` into `{name: path}`.

  Whitespace-tolerant. Skips empty entries. Raises
  DuplicateVaultNameError on collision. Raises VaultRegistryError on
  malformed entries (missing `:`, empty name, empty path).
  """
  result: dict[str, str] = {}
  for entry in raw.split(";"):
    entry = entry.strip()
    if not entry:
      continue
    if ":" not in entry:
      raise VaultRegistryError(
        f"FORGE_VAULTS entry {entry!r} is missing the `:` separator "
        f"between vault name and path. Expected 'name:path'."
      )
    name, _, path = entry.partition(":")
    name = name.strip()
    path = path.strip()
    if not name:
      raise VaultRegistryError(f"FORGE_VAULTS entry {entry!r} has an empty vault name.")
    if not path:
      raise VaultRegistryError(f"FORGE_VAULTS entry {entry!r} has an empty vault path.")
    if name in result:
      raise DuplicateVaultNameError(
        f"FORGE_VAULTS contains two vaults named {name!r}. Vault names "
        f"must be unique across the registry."
      )
    result[name] = path
  return result


class VaultRegistry:
  """Multi-vault dispatch. Constructed once at server startup; passed
  to every vault-touching tool handler.

  Preserves insertion order (dict-preserving) so `get(None)` returns the
  first-configured vault — matches driver expectation "if I don't name
  a vault, use the primary".
  """

  def __init__(self, vaults: dict[str, VaultFS]) -> None:
    if not vaults:
      raise VaultRegistryError("VaultRegistry requires at least one vault.")
    self._vaults = vaults

  @classmethod
  def from_env(cls, env: dict[str, str] | None = None) -> "VaultRegistry":
    """Construct from `FORGE_VAULTS` / `FORGE_VAULT_PATH` env vars.

    `env` param is a DI seam for tests (default: `os.environ`).
    """
    env = env if env is not None else dict(os.environ)
    raw = env.get("FORGE_VAULTS", "").strip()
    if raw:
      spec = _parse_forge_vaults_env(raw)
    else:
      legacy = env.get("FORGE_VAULT_PATH", "").strip()
      spec = {_DEFAULT_VAULT_NAME: legacy or _DEFAULT_VAULT_PATH}
    vaults: dict[str, VaultFS] = {}
    for name, path in spec.items():
      try:
        vaults[name] = VaultFS(root=Path(path).expanduser())
      except VaultFSError as exc:
        raise VaultRegistryError(
          f"Vault {name!r} at path {path!r} is not usable: {exc}"
        ) from exc
    return cls(vaults)

  def get(self, name: str | None = None) -> VaultFS:
    """Resolve a vault by name. `None` returns the first-registered."""
    if name is None or name == "":
      # dict preserves insertion order in Python 3.7+.
      first_name = next(iter(self._vaults))
      return self._vaults[first_name]
    if name not in self._vaults:
      available = ", ".join(sorted(self._vaults.keys()))
      raise VaultNotFoundError(
        f"Vault {name!r} is not registered. Available vaults: {available}. "
        f"Set FORGE_VAULTS to register additional vaults."
      )
    return self._vaults[name]

  def list(self) -> list[dict]:
    """For forge_list_vaults tool. Each entry:
    {name, path, note_count}. note_count is cheap
    (`len(VaultFS.list_notes())`) — no filter.
    """
    out: list[dict] = []
    for name, vault_fs in self._vaults.items():
      try:
        note_count = len(vault_fs.list_notes())
      except Exception:  # noqa: BLE001 — listing failure is non-fatal
        note_count = 0
      out.append({
        "name": name,
        "path": str(vault_fs.root),
        "note_count": note_count,
      })
    return out

  def names(self) -> list[str]:
    return list(self._vaults.keys())
