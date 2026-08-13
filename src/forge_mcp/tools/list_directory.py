"""`forge_list_directory` — list one directory level inside a vault.

Drain 2026-08-14-0100. Wizard could not tell an empty directory from a
nonexistent one, nor see non-`.md` content at all: `forge_read_notes_in_vault`
only surfaces Recipe notes, so a directory holding just `.mp3`/`.png` files
looked identical to an empty one.

Built as a tool rather than via MCP resources on purpose — see the drain
FEEDBACK: `ReadMcpResourceDirTool` is gated client-side ("Directory listing is
not enabled in this build", a string that appears nowhere in forge-mcp), and
forge-mcp's resource namespace addresses catalog library notes
(`forge-note:///{domain}/{name}`), not vault filesystem paths.

Path-traversal defense inherited from VaultFS._resolve_dir (same rules as
mkdir). Not recursive — descend by listing a subdirectory's path.
"""
from __future__ import annotations

from typing import Any

from ..schemas import ListDirectoryResult
from ..vault_fs import DirInvalid, DirNotFound, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_list_directory"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {
      "type": "string",
      "description": (
        "Vault-relative directory path (e.g. `experiments` or "
        "`music/sketches`). Use `.` for the vault root. Traversal "
        "patterns (`..`, hidden segments, absolute paths) are rejected."
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
  "required": ["vault", "path", "exists", "files", "directories"],
  "properties": {
    "vault": {"type": "string"},
    "path": {"type": "string"},
    "exists": {"type": "boolean"},
    "files": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "extension", "is_note", "size"],
        "properties": {
          "name": {"type": "string"},
          "extension": {"type": "string"},
          "is_note": {"type": "boolean"},
          "size": {"type": "integer"},
        },
      },
    },
    "directories": {"type": "array", "items": {"type": "string"}},
  },
}

DESCRIPTION = (
  "List one directory level inside a vault: files (with extension and an "
  "`is_note` flag for `.md`) and subdirectories, separately. An existing "
  "empty directory returns `exists: true` with empty lists; a missing one "
  "is an error with `exists: false` — the two are distinguishable. Not "
  "recursive. Pass `vault` to target a specific vault; omit for the "
  "first-registered."
)


def _error(
  text: str, *, vault: str, path: str, exists: bool = False
) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "path": path,
      "exists": exists,
      "files": [],
      "directories": [],
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

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    listing = vault_fs.listdir(path)
  except DirNotFound as exc:
    return _error(
      f"Directory not found: {exc}. It does not exist — this is different "
      f"from an existing directory that happens to be empty.",
      vault=vault_name,
      path=path,
      exists=False,
    )
  except DirInvalid as exc:
    return _error(f"Invalid directory path: {exc}", vault=vault_name, path=path)
  except VaultFSError as exc:
    return _error(f"Directory listing failed: {exc}", vault=vault_name, path=path)

  result = ListDirectoryResult(
    vault=vault_name,
    path=path.rstrip("/"),
    exists=True,
    files=listing["files"],
    directories=listing["directories"],
  )

  n_files = len(result.files)
  n_dirs = len(result.directories)
  if n_files == 0 and n_dirs == 0:
    summary = f"Directory {result.path!r} in vault {vault_name!r} exists and is empty."
  else:
    summary = (
      f"Directory {result.path!r} in vault {vault_name!r}: "
      f"{n_files} file(s), {n_dirs} subdirectory(ies)."
    )

  return {
    "content": [{"type": "text", "text": summary}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
