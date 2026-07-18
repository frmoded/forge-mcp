"""`forge_delete_note` — delete an existing note from a vault.

CW-MCP-rename-delete-note. Complements forge_rename_note so wizard can
retire notes through MCP alone.

- Path-traversal defense: `note_id` validated by `VaultFS.note_path`
  (rejects `../`, hidden segments, symlink escapes).
- If the vault is git-tracked, uses `git rm` (stages the deletion for
  the caller's next commit); otherwise plain `Path.unlink`. Deletions
  are immediate + irreversible via forge-mcp; recovery is via git for
  tracked vaults, none for untracked.
- No `force` flag. No bulk delete. One note per call.
"""
from __future__ import annotations

from typing import Any

from ..schemas import DeleteNoteResult
from ..vault_fs import NoteIdInvalid, NoteNotFound, VaultFSError, _is_git_tracked
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_delete_note"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["note_id"],
  "properties": {
    "note_id": {
      "type": "string",
      "description": (
        "Vault-relative identifier of the note to delete "
        "(e.g. `experiments/create_scale`). Trailing `.md` optional."
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
  "required": ["vault", "note_id", "path", "git_tracked"],
  "properties": {
    "vault": {"type": "string"},
    "note_id": {"type": "string"},
    "path": {"type": "string"},
    "git_tracked": {"type": "boolean"},
  },
}

DESCRIPTION = (
  "Delete an existing note from a vault. If the vault is git-tracked, "
  "uses git rm (stages the removal for the caller's next commit); "
  "otherwise plain fs unlink. Deletion is immediate + irreversible "
  "via forge-mcp (driver can restore from git for tracked vaults). "
  "Pass `vault` to target a specific vault; omit for the first-"
  "registered. Library notes cannot be deleted through this tool."
)


def _error(text: str, *, vault: str, note_id: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "note_id": note_id,
      "path": "",
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
  note_id = arguments.get("note_id")
  vault_name = arguments.get("vault")

  if not isinstance(note_id, str) or not note_id.strip():
    return _error(
      "Missing required argument: 'note_id' (vault-relative path).",
      vault=str(vault_name or ""),
      note_id="",
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), note_id=note_id)

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    removed_absolute = vault_fs.delete_note(note_id)
  except NoteIdInvalid as exc:
    return _error(f"Invalid note_id: {exc}", vault=vault_name, note_id=note_id)
  except NoteNotFound as exc:
    return _error(str(exc), vault=vault_name, note_id=note_id)
  except VaultFSError as exc:
    return _error(f"Delete failed: {exc}", vault=vault_name, note_id=note_id)

  rel_path = str(removed_absolute.relative_to(vault_fs.root))
  result = DeleteNoteResult(
    vault=vault_name,
    note_id=_normalize(note_id),
    path=rel_path,
    git_tracked=_is_git_tracked(vault_fs.root),
  )
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Deleted {_normalize(note_id)!r} from vault {vault_name!r}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
