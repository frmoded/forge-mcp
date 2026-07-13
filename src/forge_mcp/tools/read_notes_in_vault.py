"""`forge_read_notes_in_vault` — return the user's vault note list.

Wire spec: `forge-mcp-tool-surface-v1.md` §Reading/catalog.
"""
from __future__ import annotations

from typing import Any

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
)
from ..schemas import VaultListResult, VaultNoteEntry

TOOL_NAME = "forge_read_notes_in_vault"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "properties": {
    "filter": {
      "type": "string",
      "description": "Optional substring filter on note name or path",
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
        "required": [
          "note_id",
          "name",
          "path",
          "state",
          "source_facet",
          "latest_recipe_version",
        ],
        "properties": {
          "note_id": {"type": "string"},
          "name": {"type": "string"},
          "path": {"type": "string"},
          "state": {
            "type": "string",
            "description": (
              "Vault-native state label. Left open (any string) until "
              "Sprint 2 picks a concrete shape; see drain "
              "2026-07-12-1335 FEEDBACK §Fallback."
            ),
          },
          "source_facet": {
            "type": "string",
            "enum": ["description", "recipe", "python", "synced"],
            "description": (
              "Per Forge constitution §S9 (drain 2026-07-09-1600): "
              "which facet currently holds the compilable source."
            ),
          },
          "latest_recipe_version": {
            "type": "integer",
            "minimum": 0,
          },
        },
      },
    }
  },
}

DESCRIPTION = (
  "List vault notes (user-authored candidates). Optionally filter by a "
  "substring match on name or path."
)


def _summary_text(notes: list[VaultNoteEntry], filter_: str | None) -> str:
  scope = f"filter '{filter_}'" if filter_ else "no filter"
  if not notes:
    return f"No vault notes matched ({scope})."
  return f"Found {len(notes)} vault note(s) matching {scope}."


async def run(
  arguments: dict[str, Any],
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns the MCP tool-result shape."""
  filter_ = arguments.get("filter")

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      notes = await client.list_vault_notes(filter=filter_, bearer=bearer)
    except ForgeServiceEndpointMissing as exc:
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Vault list is currently unavailable: {exc}. "
              f"This is tracked in CW-MCP-1-A FEEDBACK §L47."
            ),
          }
        ],
        "structuredContent": {"notes": []},
        "isError": True,
      }
    except ForgeServiceHTTPError as exc:
      if exc.status_code in (401, 403):
        # CW-MCP-1-B — see catalog handler for context.
        return {
          "content": [
            {
              "type": "text",
              "text": (
                f"forge-transpile rejected the Bearer token (HTTP {exc.status_code} "
                f"invalid token). Rotate FORGE_MCP_BEARER in your MCP client "
                f"config and retry."
              ),
            }
          ],
          "structuredContent": {"notes": []},
          "isError": True,
        }
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Vault list request failed with HTTP {exc.status_code}. "
              f"Retry or check forge-transpile logs."
            ),
          }
        ],
        "structuredContent": {"notes": []},
        "isError": True,
      }
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  result = VaultListResult(notes=notes)
  return {
    "content": [{"type": "text", "text": _summary_text(notes, filter_)}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
