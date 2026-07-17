"""`forge_create_note` — create a fresh note with minimal frontmatter.

CW-MCP-multi-vault-create-dir. Complements forge_commit_recipe, which
requires a Recipe. This tool stages an empty note the agent can fill
later (or that the driver will fill in Obsidian).

- Creates minimal V2a shape: `---\\n---\\n\\n# Description\\n\\n<body>\\n`
- NO Recipe facet — that's commit_recipe's job.
- Fails cleanly if note exists (no overwrite).
- Parent dirs created as a side effect.
"""
from __future__ import annotations

from typing import Any

from ..schemas import CreateNoteResult
from ..vault_fs import NoteExists, NoteIdInvalid, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_create_note"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["note_id"],
  "properties": {
    "note_id": {
      "type": "string",
      "description": (
        "Vault-relative note identifier (e.g. `experiments/sketchpad`). "
        "Trailing `.md` optional. Path-traversal is rejected."
      ),
    },
    "description": {
      "type": "string",
      "description": "Optional Description body. Empty if omitted.",
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
  "Create a fresh vault note with minimal frontmatter + optional "
  "Description. No Recipe facet — use forge_commit_recipe to add one. "
  "Fails if the note already exists (no overwrite). Parent directories "
  "are created automatically. Pass `vault` to target a specific vault; "
  "omit for the first-registered."
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
  description = arguments.get("description", "")
  vault_name = arguments.get("vault")

  if not isinstance(note_id, str) or not note_id.strip():
    return _error(
      "Missing required argument: 'note_id' (vault-relative path).",
      vault=str(vault_name or ""),
      note_id="",
    )
  if not isinstance(description, str):
    return _error(
      "'description' must be a string.",
      vault=str(vault_name or ""),
      note_id=note_id,
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), note_id=note_id)

  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    absolute = vault_fs.create_note_shell(note_id, description=description)
  except NoteIdInvalid as exc:
    return _error(f"Invalid note_id: {exc}", vault=vault_name, note_id=note_id)
  except NoteExists as exc:
    return _error(
      f"{exc} Use forge_commit_recipe to overwrite the Recipe facet, "
      "or pick a different note_id.",
      vault=vault_name,
      note_id=note_id,
    )
  except VaultFSError as exc:
    return _error(f"Note creation failed: {exc}", vault=vault_name, note_id=note_id)

  rel_path = str(absolute.relative_to(vault_fs.root))
  # Normalize note_id (strip .md if caller provided it).
  normalized_note_id = note_id[:-3] if note_id.endswith(".md") else note_id
  result = CreateNoteResult(
    vault=vault_name,
    note_id=normalized_note_id,
    path=rel_path,
    absolute_path=str(absolute),
  )
  return {
    "content": [
      {
        "type": "text",
        "text": f"Created note {normalized_note_id!r} in vault {vault_name!r}.",
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
