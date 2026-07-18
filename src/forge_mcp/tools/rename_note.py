"""`forge_rename_note` — rename an existing note within a vault.

CW-MCP-rename-delete-note. Complements forge_create_note / forge_delete_note
so wizard can restructure a vault through MCP alone.

- Path-traversal defense: both `old_note_id` and `new_note_id` are
  validated by `VaultFS.note_path` (rejects `../`, hidden segments,
  symlink escapes).
- Refuses to overwrite: if the destination note exists, returns a clean
  isError. Caller must delete first if they want that flow.
- If the vault is git-tracked, uses `git mv` so history follows the
  rename; otherwise plain `Path.rename`.
- Parent directories for the new note are created if absent.
"""
from __future__ import annotations

from typing import Any

from ..schemas import RenameNoteResult
from ..vault_fs import NoteExists, NoteIdInvalid, NoteNotFound, VaultFSError, _is_git_tracked
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_rename_note"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["old_note_id", "new_note_id"],
  "properties": {
    "old_note_id": {
      "type": "string",
      "description": (
        "Vault-relative identifier of the note to rename "
        "(e.g. `experiments/sketchpad`). Trailing `.md` optional."
      ),
    },
    "new_note_id": {
      "type": "string",
      "description": (
        "Vault-relative identifier of the destination "
        "(e.g. `experiments/hello_world`). Path-traversal rejected."
      ),
    },
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Optional — defaults to "
        "the first-registered vault. Rename is same-vault only."
      ),
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": [
    "vault", "old_note_id", "new_note_id", "new_path", "absolute_path",
    "git_tracked",
  ],
  "properties": {
    "vault": {"type": "string"},
    "old_note_id": {"type": "string"},
    "new_note_id": {"type": "string"},
    "new_path": {"type": "string"},
    "absolute_path": {"type": "string"},
    "git_tracked": {"type": "boolean"},
  },
}

DESCRIPTION = (
  "Rename an existing note within a vault. Refuses to overwrite an "
  "existing destination. If the vault is git-tracked, uses git mv to "
  "preserve history; otherwise plain fs rename. Parent dirs for the "
  "new path are created automatically. Pass `vault` to target a "
  "specific vault; omit for the first-registered. Same-vault only "
  "(cross-vault move is out of scope)."
)


def _error(
  text: str, *, vault: str, old_note_id: str, new_note_id: str,
) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "old_note_id": old_note_id,
      "new_note_id": new_note_id,
      "new_path": "",
      "absolute_path": "",
      "git_tracked": False,
    },
    "isError": True,
  }


def _normalize(note_id: str) -> str:
  return note_id[:-3] if note_id.endswith(".md") else note_id


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  old_note_id = arguments.get("old_note_id")
  new_note_id = arguments.get("new_note_id")
  vault_name = arguments.get("vault")

  if not isinstance(old_note_id, str) or not old_note_id.strip():
    return _error(
      "Missing required argument: 'old_note_id' (vault-relative path).",
      vault=str(vault_name or ""),
      old_note_id="",
      new_note_id=str(new_note_id or ""),
    )
  if not isinstance(new_note_id, str) or not new_note_id.strip():
    return _error(
      "Missing required argument: 'new_note_id' (vault-relative path).",
      vault=str(vault_name or ""),
      old_note_id=old_note_id,
      new_note_id="",
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(
      str(exc),
      vault=str(vault_name or ""),
      old_note_id=old_note_id,
      new_note_id=new_note_id,
    )

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    new_absolute = vault_fs.rename_note(old_note_id, new_note_id)
  except NoteIdInvalid as exc:
    return _error(
      f"Invalid note_id: {exc}",
      vault=vault_name,
      old_note_id=old_note_id,
      new_note_id=new_note_id,
    )
  except NoteNotFound as exc:
    return _error(
      str(exc),
      vault=vault_name,
      old_note_id=old_note_id,
      new_note_id=new_note_id,
    )
  except NoteExists as exc:
    return _error(
      f"{exc} Delete the destination first if you want to overwrite.",
      vault=vault_name,
      old_note_id=old_note_id,
      new_note_id=new_note_id,
    )
  except VaultFSError as exc:
    return _error(
      f"Rename failed: {exc}",
      vault=vault_name,
      old_note_id=old_note_id,
      new_note_id=new_note_id,
    )

  new_rel = str(new_absolute.relative_to(vault_fs.root))
  result = RenameNoteResult(
    vault=vault_name,
    old_note_id=_normalize(old_note_id),
    new_note_id=_normalize(new_note_id),
    new_path=new_rel,
    absolute_path=str(new_absolute),
    git_tracked=_is_git_tracked(vault_fs.root),
  )
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Renamed {_normalize(old_note_id)!r} → "
          f"{_normalize(new_note_id)!r} in vault {vault_name!r}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
