"""`forge_check_git_lock` — read-only diagnostic for a vault's
`.git/index.lock`.

Drain 2026-09-22-1830. The vault-write tools (forge_commit_recipe,
forge_delete_note, forge_rename_note, etc.) auto-clear a stale
`.git/index.lock` before their own git operations now (see
`_clear_stale_git_lock` in vault_fs.py) — but that clearing is a side
effect of a write. This tool lets wizard/driver SEE lock state without
needing a write to fail (or succeed-by-auto-clear) first.

Never modifies anything. `would_clear_next_write` reports what WOULD
happen on the next git-touching write, without doing it.
"""
from __future__ import annotations

from typing import Any

from ..schemas import CheckGitLockResult
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_check_git_lock"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "properties": {
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Optional — defaults to "
        "the first-registered vault."
      ),
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["vault", "locked", "would_clear_next_write"],
  "properties": {
    "vault": {"type": "string"},
    "locked": {"type": "boolean"},
    "age_seconds": {"type": ["number", "null"]},
    "would_clear_next_write": {"type": "boolean"},
  },
}

DESCRIPTION = (
  "Read-only check of a vault's `.git/index.lock` state: whether it's "
  "locked, the lock's age in seconds, and whether the next git-touching "
  "forge-mcp write against this vault would auto-clear it (locks older "
  "than the staleness threshold are cleared automatically by "
  "forge_commit_recipe, forge_delete_note, forge_rename_note, and the "
  "other git-writing tools). Never modifies anything itself. Pass "
  "`vault` to target a specific vault; omit for the first-registered."
)


def _error(text: str, *, vault: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "locked": False,
      "age_seconds": None,
      "would_clear_next_write": False,
    },
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  vault_name = arguments.get("vault")

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""))

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  status = vault_fs.check_git_lock()

  result = CheckGitLockResult(
    vault=vault_name,
    locked=status["locked"],
    age_seconds=status["age_seconds"],
    would_clear_next_write=status["would_clear_next_write"],
  )

  if not result.locked:
    text = f"No git lock on vault {vault_name!r}."
  elif result.would_clear_next_write:
    text = (
      f"Vault {vault_name!r} has a git lock, age "
      f"{result.age_seconds:.0f}s — stale; the next git-touching write "
      f"will auto-clear it."
    )
  else:
    text = (
      f"Vault {vault_name!r} has a git lock, age "
      f"{result.age_seconds:.0f}s — still within the fresh window; "
      f"writes will wait for it rather than clearing it."
    )

  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
