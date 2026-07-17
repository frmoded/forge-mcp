"""`forge_create_directory` — create a directory inside a vault.

CW-MCP-multi-vault-create-dir. Enables organizational scaffolding
without needing a note-in-that-dir first.

Path-traversal defense inherited from VaultFS.mkdir. Idempotent
(mkdir -p semantics): safe to call on an existing directory.
"""
from __future__ import annotations

from typing import Any

from ..schemas import CreateDirectoryResult
from ..vault_fs import DirInvalid, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_create_directory"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {
      "type": "string",
      "description": (
        "Vault-relative directory path (e.g. `experiments` or "
        "`music/sketches`). Traversal patterns (`..`, hidden segments, "
        "absolute paths) are rejected."
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
  "required": ["vault", "path", "absolute_path"],
  "properties": {
    "vault": {"type": "string"},
    "path": {"type": "string"},
    "absolute_path": {"type": "string"},
  },
}

DESCRIPTION = (
  "Create a directory inside a vault. Idempotent (mkdir -p — safe to "
  "call on an existing directory). Path-traversal is rejected. Pass "
  "`vault` to target a specific vault; omit for the first-registered."
)


def _error(text: str, *, vault: str, path: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "path": path,
      "absolute_path": "",
    },
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

  # Resolve the effective vault name (`None` → the first-registered name)
  # so the reply names the vault the agent actually got.
  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    absolute = vault_fs.mkdir(path)
  except DirInvalid as exc:
    return _error(f"Invalid directory path: {exc}", vault=vault_name, path=path)
  except VaultFSError as exc:
    return _error(f"Directory creation failed: {exc}", vault=vault_name, path=path)

  result = CreateDirectoryResult(
    vault=vault_name,
    path=path.rstrip("/"),
    absolute_path=str(absolute),
  )
  return {
    "content": [
      {
        "type": "text",
        "text": f"Created directory {result.path!r} in vault {vault_name!r}.",
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
