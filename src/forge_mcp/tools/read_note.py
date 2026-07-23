"""`forge_read_note` — return the full V2a content of a vault note.

CW-MCP-read-note (2026-07-17). Complements
forge_read_notes_in_vault (list-with-metadata) + forge_read_note_catalog
(engine library). This is the "give me the actual bytes of one specific
vault note" surface — agents fetch an existing note as a template
without needing filesystem access via the MCP client.

Read-only. Reuses VaultFS.read_note_content + the same path-traversal
defense as commit_recipe.
"""
from __future__ import annotations

from typing import Any

from ..schemas import NoteContent, ReadNoteResult
from ..vault_fs import NoteIdInvalid, NoteNotFound, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_read_note"

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
  "required": ["note"],
  "properties": {
    "note": {
      "type": "object",
      "required": ["note_id", "vault", "raw"],
      "properties": {
        "note_id": {"type": "string"},
        "vault": {"type": "string"},
        "frontmatter": {"type": "object"},
        "description": {"type": "string"},
        "recipe": {"type": ["string", "null"]},
        "python": {"type": ["string", "null"]},
        "data": {"type": ["string", "null"]},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "raw": {"type": "string"},
      },
    }
  },
}

DESCRIPTION = (
  "Read the full V2a content of a specific vault note. Returns "
  "frontmatter (dict) + facet bodies (Description / Recipe / Python / "
  "Data) + declared inputs + the verbatim markdown source. Use this "
  "to fetch an existing note as a template for composition. "
  "Complements forge_read_notes_in_vault (list) and "
  "forge_read_note_catalog (engine library)."
)


def _error(text: str, *, note_id: str, vault: str) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "note": {
        "note_id": note_id,
        "vault": vault,
        "frontmatter": {},
        "description": "",
        "recipe": None,
        "python": None,
        "data": None,
        "inputs": [],
        "raw": "",
      },
    },
    "isError": True,
  }


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
      note_id="",
      vault=str(vault_name or ""),
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), note_id=note_id, vault=str(vault_name or ""))

  effective_vault_name = vault_name if vault_name else vault_registry.names()[0]

  try:
    content = vault_fs.read_note_content(note_id)
  except NoteIdInvalid as exc:
    return _error(
      f"Invalid note_id: {exc}", note_id=note_id, vault=effective_vault_name,
    )
  except NoteNotFound as exc:
    return _error(
      f"{exc} Use forge_read_notes_in_vault to list available notes.",
      note_id=note_id,
      vault=effective_vault_name,
    )
  except VaultFSError as exc:
    return _error(
      f"Vault read failed: {exc}", note_id=note_id, vault=effective_vault_name,
    )

  # Normalize the note_id in the response (strip .md).
  normalized_note_id = note_id[:-3] if note_id.endswith(".md") else note_id
  note_content = NoteContent(
    note_id=normalized_note_id,
    vault=effective_vault_name,
    frontmatter=content["frontmatter"],
    description=content["description"],
    recipe=content["recipe"],
    python=content["python"],
    data=content["data"],
    inputs=content["inputs"],
    raw=content["raw"],
    sync_state=content.get("sync_state"),
  )
  result = ReadNoteResult(note=note_content)
  facet_summary = []
  if note_content.description:
    facet_summary.append("Description")
  if note_content.recipe is not None:
    facet_summary.append("Recipe")
  if note_content.python is not None:
    facet_summary.append("Python")
  if note_content.data is not None:
    facet_summary.append("Data")
  facets_text = ", ".join(facet_summary) or "no facet bodies"
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Read {normalized_note_id!r} from vault {effective_vault_name!r}: "
          f"{facets_text}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
