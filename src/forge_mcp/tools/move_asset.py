"""`forge_move_asset` — relocate an existing binary vault asset.

Drain 2026-08-14-0250. Wizard was blocked retiring music-theory's top-level
`resources/` in favour of per-topic `<topic>/resources/{images,audio}/`:
`forge_render_music` and `forge_save_image_from_url` only WRITE new content
(synthesized or URL-fetched), and there is no asset-read tool to round-trip
bytes through a create call. Nothing could relocate an existing file.

Git-mv-aware via the same `VaultFS._git_aware_move` that backs
`forge_rename_note`, so history preservation and dirty-working-tree handling
are identical for assets and notes rather than a second implementation.

Parent directories of the destination are auto-created — see DESCRIPTION.
"""
from __future__ import annotations

from typing import Any

from ..schemas import MoveAssetResult
from ..vault_fs import NoteExists, NoteIdInvalid, NoteNotFound, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_move_asset"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["source_path", "dest_path"],
  "properties": {
    "source_path": {
      "type": "string",
      "description": (
        "Vault-relative path of the existing asset (e.g. "
        "`resources/images/cover.svg`)."
      ),
    },
    "dest_path": {
      "type": "string",
      "description": (
        "Vault-relative destination path (e.g. "
        "`note/resources/images/cover.svg`). Parent directories are "
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
  "required": ["vault", "source_path", "dest_path", "moved"],
  "properties": {
    "vault": {"type": "string"},
    "source_path": {"type": "string"},
    "dest_path": {"type": "string"},
    "moved": {"type": "boolean"},
    "git_sha": {"type": ["string", "null"]},
  },
}

DESCRIPTION = (
  "Move an existing binary asset (image/audio) from one path to another "
  "inside a vault. Uses `git mv` on tracked vaults so history is "
  "preserved, and commits the rename path-scoped; plain rename "
  "otherwise. Destination parent directories are created automatically. "
  "Errors if the source is missing or the destination already exists — "
  "never overwrites. Only asset extensions are permitted (not `.md` "
  "notes: use forge_rename_note). Pass `vault` to target a specific "
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
      "moved": False,
      "git_sha": None,
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
    _new_path, git_sha, _msg = vault_fs.move_asset(source_path, dest_path)
  except NoteNotFound as exc:
    return _error(
      f"Source asset not found: {exc}. Nothing was moved.",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )
  except NoteExists as exc:
    return _error(
      f"Destination already exists: {exc}. Nothing was moved — this tool "
      f"never overwrites. Choose another destination or delete the "
      f"existing file first.",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )
  except NoteIdInvalid as exc:
    return _error(
      f"Invalid asset path: {exc}",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )
  except VaultFSError as exc:
    return _error(
      f"Asset move failed: {exc}",
      vault=vault_name, source_path=source_path, dest_path=dest_path,
    )

  result = MoveAssetResult(
    vault=vault_name,
    source_path=source_path,
    dest_path=dest_path,
    moved=True,
    git_sha=git_sha,
  )
  suffix = f" (committed {git_sha[:7]})" if git_sha else ""
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Moved {source_path!r} → {dest_path!r} in vault "
          f"{vault_name!r}{suffix}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
