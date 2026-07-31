"""`forge_create_markdown_note` — create a vanilla markdown note.

CW-mcp-and-plugin-support-vanilla-notes (drain 2026-07-26-1200).

Unlike `forge_create_note` (which stamps V2a frontmatter `type: action`
+ `# Description` scaffolding for Forge action notes), this tool writes
the caller-supplied body verbatim. Use for cross-linked prose notes
(e.g. `music_theory/scale`, `music_theory/interval`) that live in a
Forge vault as regular markdown documentation.

- Refuses if the note already exists (no overwrite — use
  `forge_edit_markdown_note` for that).
- Path-traversal defense inherited from `VaultFS.note_path`.
- No frontmatter injected. Caller may write whatever markdown they
  want; empty body is allowed.
"""
from __future__ import annotations

from typing import Any

from ..schemas import CreateMarkdownNoteResult
from ..vault_fs import NoteExists, NoteIdInvalid, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_create_markdown_note"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["note_id"],
  "properties": {
    "note_id": {
      "type": "string",
      "description": (
        "Vault-relative note identifier (e.g. `music_theory/scale`). "
        "Trailing `.md` optional. Path-traversal is rejected."
      ),
    },
    "body": {
      "type": "string",
      "description": (
        "Raw markdown body to write verbatim. Empty if omitted. "
        "No frontmatter is injected — caller supplies whatever they "
        "want (or nothing)."
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
  "Create a vanilla markdown note in a vault — .md file with arbitrary "
  "body content and no Forge-managed frontmatter. For prose docs like "
  "`music_theory/scale` that live alongside Forge action notes but "
  "aren't Forge-callable. Refuses to overwrite; use forge_edit_markdown_note "
  "for that. For Forge action notes (Description + Recipe pipeline), use "
  "forge_create_note + forge_commit_recipe instead."
  "Auto-commits the written file when the vault is git-tracked and returns git_sha (null if untracked or the commit failed — the file is written either way)."
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
  body = arguments.get("body", "")
  vault_name = arguments.get("vault")

  if not isinstance(note_id, str) or not note_id.strip():
    return _error(
      "Missing required argument: 'note_id' (vault-relative path).",
      vault=str(vault_name or ""),
      note_id="",
    )
  if not isinstance(body, str):
    return _error(
      "'body' must be a string.",
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
    absolute = vault_fs.write_markdown_note(note_id, body, allow_overwrite=False)
  except NoteIdInvalid as exc:
    return _error(f"Invalid note_id: {exc}", vault=vault_name, note_id=note_id)
  except NoteExists as exc:
    return _error(
      f"{exc} Use forge_edit_markdown_note to replace body, or pick "
      "a different note_id.",
      vault=vault_name,
      note_id=note_id,
    )
  except VaultFSError as exc:
    return _error(
      f"Note creation failed: {exc}", vault=vault_name, note_id=note_id,
    )

  rel_path = str(absolute.relative_to(vault_fs.root))
  normalized_note_id = note_id[:-3] if note_id.endswith(".md") else note_id
  # drain 2026-07-31-1130 — auto-commit so wizard can ship a scaffold
  # without a second tool. Never fails the write.
  git_sha = vault_fs.auto_commit(
    absolute, f"forge_create_markdown_note: {normalized_note_id}",
  )
  result = CreateMarkdownNoteResult(
    vault=vault_name,
    note_id=normalized_note_id,
    path=rel_path,
    absolute_path=str(absolute),
    git_sha=git_sha,
  )
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Created vanilla markdown note {normalized_note_id!r} in "
          f"vault {vault_name!r}."
          + (f" Committed {git_sha[:8]}." if git_sha
             else " Not committed (vault not git-tracked).")
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
