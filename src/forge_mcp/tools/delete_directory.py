"""`forge_delete_directory` — remove an EMPTY directory inside a vault.

Drain 2026-08-14-0100. `forge_delete_note` only operates on individual note
files, and empty directories aren't git-tracked, so nothing in forge-mcp could
act on a leftover empty directory at all.

Non-empty deletion is deliberately NOT supported — explicitly rejected for this
pass, not silently deferred. A caller should remove or move contents first via
the existing note tools, then call this. Any entry counts as non-empty,
including non-`.md` files and subdirectories.

Path-traversal defense inherited from VaultFS._resolve_dir (same rules as
mkdir); the vault root itself is refused.
"""
from __future__ import annotations

from typing import Any

from ..schemas import DeleteDirectoryResult
from ..vault_fs import DirInvalid, DirNotEmpty, DirNotFound, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_delete_directory"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {
      "type": "string",
      "description": (
        "Vault-relative directory path to remove. Must be empty. "
        "Traversal patterns (`..`, hidden segments, absolute paths) are "
        "rejected, as is the vault root."
      ),
    },
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
  "required": ["vault", "path", "deleted"],
  "properties": {
    "vault": {"type": "string"},
    "path": {"type": "string"},
    "deleted": {"type": "boolean"},
  },
}

DESCRIPTION = (
  "Remove an empty directory from a vault. Errors if the directory is not "
  "empty (any file or subdirectory counts) and deletes nothing in that "
  "case — remove the contents first with forge_delete_note. Errors if the "
  "directory does not exist rather than silently succeeding. Pass `vault` "
  "to target a specific vault; omit for the first-registered."
)


def _error(text: str, *, vault: str, path: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {"vault": vault, "path": path, "deleted": False},
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  path = arguments.get("path")
  vault_name = arguments.get("vault")

  if not isinstance(path, str) or not path.strip():
    return _error(
      "Missing required argument: 'path' (vault-relative directory path).",
      vault=str(vault_name or ""),
      path="",
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), path=path)

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    vault_fs.rmdir(path)
  except DirNotEmpty as exc:
    return _error(
      f"Refusing to delete: {exc}. Nothing was deleted. Remove its "
      f"contents first (forge_list_directory shows what's inside); "
      f"recursive deletion is not supported.",
      vault=vault_name,
      path=path,
    )
  except DirNotFound as exc:
    return _error(f"Directory not found: {exc}", vault=vault_name, path=path)
  except DirInvalid as exc:
    return _error(f"Invalid directory path: {exc}", vault=vault_name, path=path)
  except VaultFSError as exc:
    return _error(f"Directory deletion failed: {exc}", vault=vault_name, path=path)

  result = DeleteDirectoryResult(
    vault=vault_name,
    path=path.rstrip("/"),
    deleted=True,
  )
  return {
    "content": [
      {
        "type": "text",
        "text": f"Deleted empty directory {result.path!r} from vault {vault_name!r}.",
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
