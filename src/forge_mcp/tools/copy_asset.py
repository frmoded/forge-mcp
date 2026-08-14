"""`forge_copy_asset` — duplicate an existing binary vault asset.

Drain 2026-08-14-0250, sibling of `forge_move_asset`. Same motivation: no
existing tool could take an asset already in the vault and put its bytes
somewhere else.

Copy is not a native git operation the way `mv` is, so on a tracked vault this
is a filesystem copy followed by `git add` of the destination. Unlike a move it
is deliberately NOT auto-committed — a copy leaves the source in place, so
there is no delete+add pair that must land atomically.
"""
from __future__ import annotations

from typing import Any

from ..schemas import CopyAssetResult
from ..vault_fs import NoteExists, NoteIdInvalid, NoteNotFound, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_copy_asset"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["source_path", "dest_path"],
  "properties": {
    "source_path": {
      "type": "string",
      "description": (
        "Vault-relative path of the existing asset to duplicate."
      ),
    },
    "dest_path": {
      "type": "string",
      "description": (
        "Vault-relative destination path. Parent directories are "
        "created if missing. Must not already exist."
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
  "required": ["vault", "source_path", "dest_path", "copied", "staged"],
  "properties": {
    "vault": {"type": "string"},
    "source_path": {"type": "string"},
    "dest_path": {"type": "string"},
    "copied": {"type": "boolean"},
    "staged": {"type": "boolean"},
  },
}

DESCRIPTION = (
  "Copy an existing binary asset (image/audio) to another path inside a "
  "vault, leaving the original in place. On git-tracked vaults the new "
  "file is `git add`ed but NOT committed. Destination parent "
  "directories are created automatically. Errors if the source is "
  "missing or the destination already exists — never overwrites. Only "
  "asset extensions are permitted. Pass `vault` to target a specific "
  "vault; omit for the first-registered."
)


def _error(
  text: str, *, vault: str, source_path: str, dest_path: str
) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "source_path": source_path,
      "dest_path": dest_path,
      "copied": False,
      "staged": False,
    },
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  source_path = arguments.get("source_path")
  dest_path = arguments.get("dest_path")
  vault_name = arguments.get("vault")

  for name, value in (("source_path", source_path), ("dest_path", dest_path)):
    if not isinstance(value, str) or not value.strip():
      return _error(
        f"Missing required argument: {name!r} (vault-relative asset path).",
        vault=str(vault_name or ""),
        source_path=source_path if isinstance(source_path, str) else "",
        dest_path=dest_path if isinstance(dest_path, str) else "",
      )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(
      str(exc), vault=str(vault_name or ""),
      source_path=source_path, dest_path=dest_path,
    )

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    _dst, staged = vault_fs.copy_asset(source_path, dest_path)
  except NoteNotFound as exc:
    return _error(
      f"Source asset not found: {exc}. Nothing was copied.",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )
  except NoteExists as exc:
    return _error(
      f"Destination already exists: {exc}. Nothing was copied — this "
      f"tool never overwrites.",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )
  except NoteIdInvalid as exc:
    return _error(
      f"Invalid asset path: {exc}",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )
  except VaultFSError as exc:
    return _error(
      f"Asset copy failed: {exc}",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )

  result = CopyAssetResult(
    vault=vault_name,
    source_path=source_path,
    dest_path=dest_path,
    copied=True,
    staged=staged,
  )
  suffix = " and staged with git add" if staged else ""
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Copied {source_path!r} → {dest_path!r} in vault "
          f"{vault_name!r}{suffix}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
