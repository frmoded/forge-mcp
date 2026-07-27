"""`forge_edit_markdown_note` — full-body replace on a vanilla note.

CW-mcp-and-plugin-support-vanilla-notes (drain 2026-07-26-1200).

Complements forge_create_markdown_note: this tool overwrites an
existing note's raw markdown. Refuses to edit action notes (`type:
action` frontmatter) — those must go through `forge_commit_recipe`
so version bumping + English-hash stamping stay consistent.

Data notes (`type: data`) are also refused by default: they're a
Forge concept and the driver should shape a `forge_edit_data_note`
tool with its own semantics if that surface ever materializes (per
drain spec §Not in scope). Vanilla notes are the sole allowed target.
"""
from __future__ import annotations

from typing import Any

from ..schemas import EditMarkdownNoteResult
from ..vault_fs import NoteIdInvalid, NoteNotFound, VaultFSError, _classify_note_type
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_edit_markdown_note"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["note_id", "body"],
  "properties": {
    "note_id": {
      "type": "string",
      "description": (
        "Vault-relative note identifier of an EXISTING vanilla note. "
        "Trailing `.md` optional. Path-traversal is rejected."
      ),
    },
    "body": {
      "type": "string",
      "description": (
        "Full replacement markdown body, written verbatim. Empty "
        "string clears the note."
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
  "required": ["vault", "note_id", "path", "absolute_path"],
  "properties": {
    "vault": {"type": "string"},
    "note_id": {"type": "string"},
    "path": {"type": "string"},
    "absolute_path": {"type": "string"},
  },
}

DESCRIPTION = (
  "Full-body replace on an existing vanilla markdown note. Fails if "
  "the note doesn't exist, or if it's a Forge action or data note "
  "(use forge_commit_recipe for action notes). Writes body verbatim "
  "with no auto-frontmatter injection. Pair with forge_create_markdown_note "
  "for the create side."
)


def _error(text: str, *, vault: str, note_id: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "note_id": note_id,
      "path": "",
      "absolute_path": "",
    },
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  note_id = arguments.get("note_id")
  body = arguments.get("body")
  vault_name = arguments.get("vault")

  if not isinstance(note_id, str) or not note_id.strip():
    return _error(
      "Missing required argument: 'note_id' (vault-relative path).",
      vault=str(vault_name or ""),
      note_id="",
    )
  if not isinstance(body, str):
    return _error(
      "Missing required argument: 'body' (must be a string; empty "
      "string is allowed).",
      vault=str(vault_name or ""),
      note_id=note_id,
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), note_id=note_id)

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  # Read first — needed to (a) confirm existence and (b) classify the
  # note type. Both errors surface with actionable guidance.
  try:
    content = vault_fs.read_note_content(note_id)
  except NoteIdInvalid as exc:
    return _error(f"Invalid note_id: {exc}", vault=vault_name, note_id=note_id)
  except NoteNotFound as exc:
    return _error(
      f"{exc} Use forge_create_markdown_note to create a new note.",
      vault=vault_name,
      note_id=note_id,
    )
  except VaultFSError as exc:
    return _error(f"Vault read failed: {exc}", vault=vault_name, note_id=note_id)

  note_type = _classify_note_type(content["frontmatter"])
  if note_type == "action":
    return _error(
      f"Refusing to edit {note_id!r}: it is a Forge action note "
      "(`type: action`). Use forge_commit_recipe to update its "
      "Recipe/Python facets so recipe_version and hashes stay "
      "consistent.",
      vault=vault_name,
      note_id=note_id,
    )
  if note_type == "data":
    return _error(
      f"Refusing to edit {note_id!r}: it is a Forge data note "
      "(`type: data`). Data-note editing is not part of this tool's "
      "surface — hand-edit the file in Obsidian, or wait for a "
      "future forge_edit_data_note tool.",
      vault=vault_name,
      note_id=note_id,
    )

  try:
    absolute = vault_fs.write_markdown_note(note_id, body, allow_overwrite=True)
  except NoteIdInvalid as exc:
    # Redundant given the successful read above, but kept for symmetry.
    return _error(f"Invalid note_id: {exc}", vault=vault_name, note_id=note_id)
  except VaultFSError as exc:
    return _error(f"Note write failed: {exc}", vault=vault_name, note_id=note_id)

  rel_path = str(absolute.relative_to(vault_fs.root))
  normalized_note_id = note_id[:-3] if note_id.endswith(".md") else note_id
  result = EditMarkdownNoteResult(
    vault=vault_name,
    note_id=normalized_note_id,
    path=rel_path,
    absolute_path=str(absolute),
  )
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Edited vanilla markdown note {normalized_note_id!r} in "
          f"vault {vault_name!r}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
