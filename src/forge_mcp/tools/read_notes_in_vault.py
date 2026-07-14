"""`forge_read_notes_in_vault` — list vault notes.

Drain CW-MCP-2-E — LOCAL VaultFS-backed handler (Option C, same
architecture as CW-MCP-2-C's `forge_commit_recipe`). Pre-drain this
tool proxied `forge-transpile /vault/notes`, an endpoint that has
NEVER been implemented; every call silently returned an "endpoint
missing" isError. Now it walks the same local vault that
`forge_commit_recipe` writes to → symmetric read/write surface,
single source of truth.

Wire spec: `forge-mcp-tool-surface-v1.md` §Reading — reshaped from the
Sprint 1 speculative fields (`state`, `source_facet`,
`latest_recipe_version`) to the fields the local walker can actually
populate. Richer per-note metadata (state / source_facet computation)
is deferred to a future `forge_describe_note` polish drain.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..schemas import VaultListResult, VaultNoteEntry
from ..vault_fs import VaultFS, VaultFSError

TOOL_NAME = "forge_read_notes_in_vault"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "properties": {
    "filter": {
      "type": "string",
      "description": "Optional substring filter on note_id (case-sensitive).",
    }
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["notes"],
  "properties": {
    "notes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["note_id", "name", "path", "has_recipe"],
        "properties": {
          "note_id": {"type": "string"},
          "name": {"type": "string"},
          "path": {"type": "string"},
          "has_recipe": {"type": "boolean"},
          "recipe_version": {"type": ["integer", "null"], "minimum": 0},
        },
      },
    }
  },
}

DESCRIPTION = (
  "List vault notes. Walks the local vault directory (set by "
  "FORGE_VAULT_PATH env, default ~/forge-vaults/bluh) and returns one "
  "entry per `.md` file with `{note_id, name, path, has_recipe, "
  "recipe_version}`. Optional `filter` argument does a substring match "
  "on note_id. Hidden dirs (`.obsidian/`, `.git/`, etc.) are excluded. "
  "Symmetric with forge_commit_recipe — both read/write the same vault."
)


def _vault_root_from_env() -> Path:
  """Read `FORGE_VAULT_PATH` from the environment (default:
  `~/forge-vaults/bluh`). Same env-var convention as
  `commit_recipe._vault_root_from_env` so the two tools stay in sync."""
  raw = os.environ.get("FORGE_VAULT_PATH", "~/forge-vaults/bluh").strip()
  return Path(raw).expanduser()


def _summary_text(notes: list[VaultNoteEntry], filter_: str | None) -> str:
  scope = f"filter '{filter_}'" if filter_ else "no filter"
  if not notes:
    return f"No vault notes matched ({scope})."
  return f"Found {len(notes)} vault note(s) matching {scope}."


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — kept for wrapper-signature symmetry with other tools
  vault_fs: VaultFS | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns the MCP tool-result shape.

  `vault_fs` param is a dependency-injection seam for tests; production
  callers pass None → constructed from env. `bearer` is kept in the
  signature so the FastMCP wrapper's call site stays symmetric with the
  other tools (see server.py::_forge_read_notes_in_vault), but the
  local read path doesn't need it — no upstream forge-transpile call.
  """
  filter_ = arguments.get("filter")

  if vault_fs is None:
    try:
      vault_fs = VaultFS(root=_vault_root_from_env())
    except VaultFSError as exc:
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Vault filesystem unavailable: {exc}. Set FORGE_VAULT_PATH "
              "to an existing vault directory in the forge-mcp environment."
            ),
          }
        ],
        "structuredContent": {"notes": []},
        "isError": True,
      }

  raw_entries = vault_fs.list_notes(filter=filter_)
  notes = [VaultNoteEntry.model_validate(e) for e in raw_entries]
  result = VaultListResult(notes=notes)
  return {
    "content": [{"type": "text", "text": _summary_text(notes, filter_)}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
