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
    "message": {
      "type": "string",
      "description": (
        "Optional commit message override (git-tracked vaults only). "
        "Defaults to `delete note <note_id>` when omitted. Ignored on "
        "untracked vaults."
      ),
    },
    "is_asset": {
      "type": "boolean",
      "default": False,
      "description": (
        "False (default) deletes a `.md` note. True deletes a non-.md "
        "vault asset written by forge_render_viz / forge_render_music / "
        "forge_save_image_from_url — .svg .png .jpg .jpeg .webp .gif "
        ".mp3 .mid .midi .wav .musicxml .xml. Extensions outside that "
        "list are refused either way, so this can never delete source "
        "files. Path-safety is identical for both."
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
    "git_tracked": {
      "type": "boolean",
      "description": (
        "True iff the vault is a git repo. When True, unstaged "
        "working-tree modifications to the target file are discarded "
        "via `git checkout HEAD --` before `git rm`; delete is thus "
        "destructive of any transient in-flight facet re-derivations."
      ),
    },
    "git_sha": {
      "type": ["string", "null"],
      "description": (
        "40-char SHA of the auto-created delete commit "
        "(drain 2026-07-24-1500). Null when the vault isn't git-tracked "
        "or when the file was never in HEAD (nothing to commit)."
      ),
    },
    "message": {
      "type": ["string", "null"],
      "description": "The commit message actually used, or null when no commit was made.",
    },
  },
}

DESCRIPTION = (
  "Delete an existing note from a vault. If the vault is git-tracked, "
  "uses git rm + auto-commits the removal (returns git_sha) — mirrors "
  "forge_commit_recipe's contract per drain 2026-07-24-1500. Otherwise "
  "plain fs unlink and git_sha is null. Deletion is immediate + "
  "irreversible via forge-mcp (driver can restore from git for tracked "
  "vaults, e.g. `git revert <git_sha>`). On git-tracked vaults, any "
  "unstaged working-tree modifications to the target are discarded "
  "(git checkout HEAD) before removal — delete means remove the note "
  "entirely, including transient in-flight edits. Auto-commit is "
  "path-scoped so unrelated staged changes elsewhere in the vault are "
  "NOT swept in. Pass `vault` to target a specific vault; omit for "
  "the first-registered. Pass optional `message` to override the "
  "default `delete note <note_id>` commit subject. Library notes "
  "cannot be deleted through this tool."
)


def _error(text: str, *, vault: str, note_id: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "note_id": note_id,
      "path": "",
      "git_tracked": False,
      "git_sha": None,
      "message": None,
    },
    "isError": True,
  }


def _normalize(note_id: str, *, is_asset: bool = False) -> str:
  # Assets keep their extension — `pitch.svg` and `pitch.mp3` are
  # different files, so stripping the suffix would make the reported
  # id ambiguous. Only `.md` notes get the historical strip.
  if is_asset:
    return note_id
  return note_id[:-3] if note_id.endswith(".md") else note_id


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  note_id = arguments.get("note_id")
  vault_name = arguments.get("vault")
  message = arguments.get("message")
  is_asset = bool(arguments.get("is_asset", False))

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
    removed_absolute, git_sha, commit_msg = vault_fs.delete_note(
      note_id,
      message=message if isinstance(message, str) else None,
      is_asset=is_asset,
    )
  except NoteIdInvalid as exc:
    return _error(f"Invalid note_id: {exc}", vault=vault_name, note_id=note_id)
  except NoteNotFound as exc:
    return _error(str(exc), vault=vault_name, note_id=note_id)
  except VaultFSError as exc:
    return _error(f"Delete failed: {exc}", vault=vault_name, note_id=note_id)

  rel_path = str(removed_absolute.relative_to(vault_fs.root))
  result = DeleteNoteResult(
    vault=vault_name,
    note_id=_normalize(note_id, is_asset=is_asset),
    path=rel_path,
    git_tracked=_is_git_tracked(vault_fs.root),
    git_sha=git_sha,
    message=commit_msg,
  )
  sha_suffix = f" @ {git_sha[:7]}" if git_sha else ""
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Deleted {'asset' if is_asset else 'note'} "
          f"{_normalize(note_id, is_asset=is_asset)!r} from vault "
          f"{vault_name!r}{sha_suffix}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
