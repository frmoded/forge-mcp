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
from .vault_imports import detect_cycle, parse_imports


class VaultRegistryError(Exception):
  """Base for registry errors."""


class VaultImportCycleError(VaultRegistryError):
  """A vault's [imports] graph contains a cycle. Message carries the
  full cycle path (per drain 1710's detect_cycle contract — the whole
  loop is actionable; 'cycle detected' alone is not)."""


class VaultNotFoundError(VaultRegistryError):
  """No vault registered under the requested name."""


class DuplicateVaultNameError(VaultRegistryError):
  """FORGE_VAULTS contained two entries with the same name."""


class LastVaultRemovalError(VaultRegistryError):
  """Refused to remove the only remaining vault. Safety invariant per
  CW-MCP-runtime-vault-registration §4.3 — an empty registry leaves
  subsequent tool calls with nothing to target."""


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
    # Drain 2026-08-05-1900 (vault-split 3b) — import roots per
    # top-level vault: vault name -> import name -> VaultFS. Import
    # roots are NOT top-level vaults: they never appear in list() /
    # names() / get(); they are reachable only via the vault that
    # imports them (get_import_roots / get_import_root).
    self._import_roots: dict[str, dict[str, VaultFS]] = {}
    for name in self._vaults:
      self._scan_imports(name)

  def _scan_imports(self, name: str) -> None:
    """Parse `[imports]` from the vault's forge.toml; register roots;
    reject cycles.

    Loud by design: a bad import declaration (unresolvable path, name
    disagreement, git-only entry pre-Phase-2b, cycle) raises at
    registration time rather than surfacing as a mysteriously
    unresolvable wikilink later — same posture as parse_imports itself
    and the startup DuplicateVaultNameError.
    """
    vault_fs = self._vaults[name]
    manifest = vault_fs.root / "forge.toml"
    if not manifest.exists():
      self._import_roots[name] = {}
      return
    decls = parse_imports(manifest)
    roots: dict[str, VaultFS] = {}
    for import_name, decl in decls.items():
      # parse_imports already validated: root is a directory, has a
      # forge.toml, and its declared name matches the import key.
      roots[import_name] = VaultFS(root=decl.root)
    self._import_roots[name] = roots
    if decls:
      self._reject_import_cycles(name, vault_fs.root)

  def _reject_import_cycles(self, name: str, root: Path) -> None:
    """Build the TRANSITIVE import graph (nodes keyed by resolved
    path — import names are local aliases and cannot carry identity
    across vaults) and raise on any cycle reachable from `root`.

    Transitive imports are parsed here for cycle detection ONLY; they
    are not registered as walkable roots in this phase (the walker
    resolves one level of imports — an imported vault's own imports
    are Phase-3 follow-up scope)."""
    edges: dict[str, list[str]] = {}
    pending = [root.resolve()]
    while pending:
      node = pending.pop()
      key = str(node)
      if key in edges:
        continue
      manifest = node / "forge.toml"
      if not manifest.exists():
        edges[key] = []
        continue
      decls = parse_imports(manifest)
      children = [decl.root.resolve() for decl in decls.values()]
      edges[key] = [str(c) for c in children]
      pending.extend(children)
    cycle = detect_cycle(str(root.resolve()), edges)
    if cycle:
      pretty = " → ".join(Path(p).name for p in cycle)
      raise VaultImportCycleError(
        f"Vault {name!r} has an import cycle: {pretty}. Imports must "
        f"form a DAG — break the loop in one of the forge.toml "
        f"[imports] tables."
      )

  @classmethod
  def from_env(cls, env: dict[str, str] | None = None) -> VaultRegistry:
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

  # -- Import roots (drain 2026-08-05-1900, vault-split 3b) ------------------

  def get_import_roots(self, name: str | None = None) -> dict[str, VaultFS]:
    """The import roots of one top-level vault: import name -> VaultFS.

    Empty dict for a vault with no [imports] — the common case.
    Declaration order is preserved (parse_imports iterates the TOML
    table), which the link resolver's bare-name search relies on.
    `None` resolves to the first-registered vault, mirroring `get`.
    """
    if name is None or name == "":
      name = next(iter(self._vaults))
    if name not in self._vaults:
      available = ", ".join(sorted(self._vaults.keys()))
      raise VaultNotFoundError(
        f"Vault {name!r} is not registered. Available vaults: {available}."
      )
    return dict(self._import_roots.get(name, {}))

  def get_import_root(self, name: str, import_name: str) -> VaultFS:
    """Direct lookup of one import root. Raises VaultNotFoundError for
    an unknown vault OR an undeclared import name."""
    roots = self.get_import_roots(name)
    if import_name not in roots:
      declared = ", ".join(roots) or "none"
      raise VaultNotFoundError(
        f"Vault {name!r} declares no import {import_name!r}. "
        f"Declared imports: {declared}."
      )
    return roots[import_name]

  # -- Runtime add / remove (CW-MCP-runtime-vault-registration) -------------

  def add(self, name: str, vault_fs: VaultFS) -> None:
    """Register a new vault at runtime.

    Raises DuplicateVaultNameError if `name` is already registered;
    caller must remove first (no silent overwrite).
    """
    if name in self._vaults:
      raise DuplicateVaultNameError(
        f"Vault {name!r} is already registered. Unregister first, then re-add."
      )
    self._vaults[name] = vault_fs
    try:
      self._scan_imports(name)
    except Exception:
      # A vault with a broken [imports] must not half-register — roll
      # back so the registry's vault set and import-root set stay
      # consistent, then let the error surface to the caller.
      del self._vaults[name]
      self._import_roots.pop(name, None)
      raise

  def remove(self, name: str) -> None:
    """Unregister a vault at runtime.

    Raises VaultNotFoundError if `name` isn't registered.
    Raises LastVaultRemovalError if `name` is the only vault left —
    the registry must always retain at least one entry so subsequent
    tool calls have somewhere to target.

    Filesystem side effects: NONE. Files stay put; only the mapping
    is removed.
    """
    if name not in self._vaults:
      available = ", ".join(sorted(self._vaults.keys()))
      raise VaultNotFoundError(
        f"Vault {name!r} is not registered. Available vaults: {available}."
      )
    if len(self._vaults) == 1:
      raise LastVaultRemovalError(
        f"Cannot remove {name!r}: it is the only remaining registered vault. "
        f"Register another vault first, then remove this one."
      )
    del self._vaults[name]
    self._import_roots.pop(name, None)
