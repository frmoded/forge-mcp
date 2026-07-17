"""`forge_register_vault` — runtime vault registration.

CW-MCP-runtime-vault-registration. Adds a vault to the live
VaultRegistry without editing FORGE_VAULTS + restarting the server.

Path validation:
  1. `~` expansion.
  2. Must be absolute (no cwd ambiguity).
  3. Must exist.
  4. Must be a directory.
  5. Must be writable (os.W_OK).

Duplicate names are rejected — no silent overwrite. Caller must
`forge_unregister_vault` first.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..schemas import RegisterVaultResult, VaultEntry
from ..vault_fs import VaultFS, VaultFSError
from ..vault_registry import DuplicateVaultNameError, VaultRegistry

TOOL_NAME = "forge_register_vault"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["name", "path"],
  "properties": {
    "name": {
      "type": "string",
      "description": (
        "Short identifier for this vault; must not conflict with existing "
        "vault names (see forge_list_vaults)."
      ),
    },
    "path": {
      "type": "string",
      "description": (
        "Absolute path to the vault directory. `~` is expanded. Relative "
        "paths are rejected (no cwd ambiguity). Must be an existing, "
        "writable directory."
      ),
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["registered_vault"],
  "properties": {
    "registered_vault": {
      "type": "object",
      "required": ["name", "path", "note_count"],
      "properties": {
        "name": {"type": "string"},
        "path": {"type": "string"},
        "note_count": {"type": "integer", "minimum": 0},
      },
    }
  },
}

DESCRIPTION = (
  "Register a new vault with the live forge-mcp registry at runtime. "
  "Path must be an existing, writable directory (absolute, `~`-"
  "expanded). Duplicate names are rejected — unregister first to "
  "replace. Runtime registrations are in-memory ONLY; they do NOT "
  "persist across server restart. Use FORGE_VAULTS for durable "
  "configuration."
)


def _error(text: str, *, name: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "registered_vault": {"name": name, "path": "", "note_count": 0},
    },
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  name = arguments.get("name")
  raw_path = arguments.get("path")

  if not isinstance(name, str) or not name.strip():
    return _error("Missing required argument: 'name' (vault identifier).", name="")
  if not isinstance(raw_path, str) or not raw_path.strip():
    return _error(
      "Missing required argument: 'path' (absolute vault directory path).",
      name=name,
    )

  # 1. `~` expansion.
  expanded = os.path.expanduser(raw_path.strip())
  # 2. Absolute path required (rejects relative).
  if not os.path.isabs(expanded):
    return _error(
      f"Path {raw_path!r} is not absolute. Provide an absolute path "
      f"(e.g. '/Users/you/vault' or '~/vault').",
      name=name,
    )
  target = Path(expanded)
  # 3. Existence check.
  if not target.exists():
    return _error(
      f"Path {expanded!r} does not exist. Create the directory first, "
      f"then retry forge_register_vault.",
      name=name,
    )
  # 4. Directory check.
  if not target.is_dir():
    return _error(
      f"Path {expanded!r} is not a directory. forge_register_vault "
      f"targets directories, not files.",
      name=name,
    )
  # 5. Writable check.
  if not os.access(expanded, os.W_OK):
    return _error(
      f"Path {expanded!r} is not writable. Fix permissions and retry.",
      name=name,
    )

  # Construct VaultFS + add to registry.
  try:
    vault_fs = VaultFS(root=target)
  except VaultFSError as exc:
    return _error(f"Vault filesystem unavailable: {exc}", name=name)

  try:
    vault_registry.add(name, vault_fs)
  except DuplicateVaultNameError as exc:
    return _error(str(exc), name=name)

  try:
    note_count = len(vault_fs.list_notes())
  except Exception:  # noqa: BLE001 — listing failure is non-fatal
    note_count = 0

  entry = VaultEntry(name=name, path=str(vault_fs.root), note_count=note_count)
  result = RegisterVaultResult(registered_vault=entry)
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Registered vault {name!r} at {vault_fs.root} ({note_count} notes). "
          f"Runtime-only — set FORGE_VAULTS to persist across restart."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
