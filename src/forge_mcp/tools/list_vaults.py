"""`forge_list_vaults` — enumerate registered vaults.

CW-MCP-multi-vault-create-dir. Returns the vaults registered via
FORGE_VAULTS (or the single default when FORGE_VAULT_PATH is used).

Agents call this to discover which vaults they can target, then pass
the `vault` param on subsequent tool calls (commit_recipe,
read_notes_in_vault, create_directory, create_note).
"""
from __future__ import annotations

from typing import Any

from ..schemas import ListVaultsResult, VaultEntry
from ..vault_registry import VaultRegistry

TOOL_NAME = "forge_list_vaults"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "properties": {},
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["vaults"],
  "properties": {
    "vaults": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "path", "note_count"],
        "properties": {
          "name": {"type": "string"},
          "path": {"type": "string"},
          "note_count": {"type": "integer", "minimum": 0},
        },
      },
    }
  },
}

DESCRIPTION = (
  "List the vaults registered with this forge-mcp instance. Each entry "
  "has {name, path, note_count}. Configure via the FORGE_VAULTS env var "
  "(colon-separated name:path, semicolon between entries) — legacy "
  "FORGE_VAULT_PATH remains supported as a single 'default' vault. "
  "Agents call this once at conversation start, then thread the chosen "
  "`vault` param on subsequent vault-touching tools."
)


async def run(
  arguments: dict[str, Any],  # noqa: ARG001 — no inputs
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  entries = vault_registry.list()
  vault_entries = [VaultEntry.model_validate(e) for e in entries]
  result = ListVaultsResult(vaults=vault_entries)
  names = ", ".join(e.name for e in vault_entries) or "(none)"
  return {
    "content": [
      {"type": "text", "text": f"Registered vaults: {names}."},
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
