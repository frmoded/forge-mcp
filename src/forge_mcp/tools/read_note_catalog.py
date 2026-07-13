"""`forge_read_note_catalog` — return the library note catalog.

Wire spec: `forge-mcp-tool-surface-v1.md` §Reading/catalog.
"""
from __future__ import annotations

from typing import Any

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
)
from ..schemas import NoteCatalogResult, NoteEntry

TOOL_NAME = "forge_read_note_catalog"

# Hand-written inputSchema — matches v1 spec §Reading/catalog verbatim so
# spec drift is a review-visible diff, not a silent Pydantic-side rename.
INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "properties": {
    "domain": {
      "type": "string",
      "description": (
        "Optional domain filter (e.g., 'music', 'moda'). Omit to list all domains."
      ),
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
          "name",
          "domain",
          "signature",
          "short_desc",
          "long_desc",
          "uri",
        ],
        "properties": {
          "name": {"type": "string"},
          "domain": {"type": "string"},
          "signature": {
            "type": "string",
            "description": (
              "E-- signature: `Call [[name]] with param1=type1, "
              "param2=type2` returning type"
            ),
          },
          "short_desc": {
            "type": "string",
            "description": "One-line description, <=120 chars",
          },
          "long_desc": {
            "type": "string",
            "description": (
              "Paragraph description including usage guidance and typical compositions"
            ),
          },
          "uri": {
            "type": "string",
            "format": "uri",
            "description": (
              "forge-note:///{domain}/{name} — stable identifier + future extension point"
            ),
          },
        },
      },
    }
  },
}

DESCRIPTION = (
  "Return the E-- library note catalog. Optionally filter by domain. "
  "Every entry includes the E-- signature the agent needs to call the note."
)


def _summary_text(notes: list[NoteEntry], domain: str | None) -> str:
  scope = f"domain '{domain}'" if domain else "all domains"
  if not notes:
    return f"No notes found in the catalog for {scope}."
  return f"Found {len(notes)} note(s) in {scope}."


async def run(
  arguments: dict[str, Any],
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns the MCP tool-result shape.

  Result shape follows the "content + structuredContent + isError" convention
  from the v1 spec §Return shape.
  """
  domain = arguments.get("domain")

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      notes = await client.get_catalog(domain=domain, bearer=bearer)
    except ForgeServiceEndpointMissing as exc:
      # forge-transpile /catalog isn't shipped yet — surface as a business
      # error so the agent gets a clean actionable message rather than a
      # protocol-level fault.
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Catalog is currently unavailable: {exc}. "
              f"This is tracked in CW-MCP-1-A FEEDBACK §L47."
            ),
          }
        ],
        "structuredContent": {"notes": []},
        "isError": True,
      }
    except ForgeServiceHTTPError as exc:
      if exc.status_code in (401, 403):
        # CW-MCP-1-B — forge-transpile rejected the Bearer. forge-mcp
        # does not validate tokens itself (forge-transpile is source of
        # truth), so surface the rejection with the exact code so the
        # agent knows to rotate/replace its token.
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
              f"Catalog request failed with HTTP {exc.status_code}. "
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

  # Domain-filtered request that returned zero notes is treated as
  # "unknown domain" per §Reading/catalog Errors.
  if domain is not None and not notes:
    return {
      "content": [
        {
          "type": "text",
          "text": (
            f"No notes found for domain '{domain}'. "
            f"Domain may not exist. Try calling forge_read_note_catalog with "
            f"no domain to list all available domains."
          ),
        }
      ],
      "structuredContent": {"notes": []},
      "isError": True,
    }

  result = NoteCatalogResult(notes=notes)
  return {
    "content": [{"type": "text", "text": _summary_text(notes, domain)}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
