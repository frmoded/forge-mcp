"""`forge-note:///{domain}/{name}` URI parser + resources/list + resources/read.

Wire spec: `forge-mcp-tool-surface-v1.md` §Resource URI schemes.

The URI is a triple-slash form because it has no authority component
(RFC 3986 §3.2). Both `domain` and `name` are opaque path components
we don't URL-encode further — they are ASCII identifiers by convention.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
)
from ..schemas import NoteEntry

SCHEME = "forge-note"


def build_forge_note_uri(domain: str, name: str) -> str:
  """Format a canonical forge-note URI. Neither arg may contain '/'."""
  if "/" in domain:
    raise ValueError(f"domain may not contain '/': {domain!r}")
  if "/" in name:
    raise ValueError(f"name may not contain '/': {name!r}")
  return f"{SCHEME}:///{domain}/{name}"


def parse_forge_note_uri(uri: str) -> tuple[str, str]:
  """Extract (domain, name) from a forge-note URI.

  Rejects: wrong scheme, missing or extra path segments, empty components,
  URIs with an authority (host) component, or surrounding whitespace.
  """
  if not isinstance(uri, str):
    raise ValueError(f"URI must be a string, got {type(uri).__name__}")
  if uri != uri.strip():
    raise ValueError(f"URI contains leading/trailing whitespace: {uri!r}")

  parsed = urlparse(uri)
  if parsed.scheme != SCHEME:
    raise ValueError(
      f"Invalid scheme: expected {SCHEME!r}, got {parsed.scheme!r} in {uri!r}"
    )
  # forge-note:///domain/name has empty netloc; forge-note://host/... has one.
  if parsed.netloc:
    raise ValueError(
      f"forge-note URIs must have empty authority (triple slash), got {uri!r}"
    )

  # parsed.path is like '/music/compose_blues'. Strip leading '/'.
  path = parsed.path
  if not path.startswith("/"):
    raise ValueError(f"forge-note URI path must start with '/', got {uri!r}")
  parts = path[1:].split("/")
  if len(parts) != 2:
    raise ValueError(
      f"forge-note URI must have exactly two path segments (domain/name), got {uri!r}"
    )

  domain, name = parts
  if not domain:
    raise ValueError(f"forge-note URI has empty domain segment: {uri!r}")
  if not name:
    raise ValueError(f"forge-note URI has empty name segment: {uri!r}")
  if parsed.query or parsed.fragment:
    raise ValueError(f"forge-note URI must not include query or fragment: {uri!r}")

  return domain, name


# -----------------------------------------------------------------------------
# resources/list + resources/read
# -----------------------------------------------------------------------------


def _entry_to_resource_descriptor(entry: NoteEntry) -> dict[str, Any]:
  """Shape the MCP resources/list expects for each entry."""
  return {
    "uri": entry.uri,
    "name": entry.name,
    "description": entry.short_desc,
    "mimeType": "application/vnd.forge.note+json",
  }


async def list_note_resources(
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> list[dict[str, Any]]:
  """Return all forge-note URIs from the catalog as resource descriptors."""
  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      notes = await client.get_catalog(domain=None, bearer=bearer)
    except ForgeServiceEndpointMissing:
      # Nothing to enumerate until forge-transpile ships /catalog.
      return []
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  return [_entry_to_resource_descriptor(n) for n in notes]


async def read_note_resource(
  uri: str,
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Fetch a single note by URI. Returns MCP resource-read content shape.

  Raises ValueError on malformed URI (which the server maps to a
  protocol-level -32602 invalid params). A missing note is returned as
  an empty structured content with a text hint.
  """
  domain, name = parse_forge_note_uri(uri)

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      notes = await client.get_catalog(domain=domain, bearer=bearer)
    except ForgeServiceEndpointMissing as exc:
      return {
        "contents": [
          {
            "uri": uri,
            "mimeType": "text/plain",
            "text": (
              f"Catalog is currently unavailable: {exc}. "
              f"Cannot fetch {uri}."
            ),
          }
        ],
      }
    except ForgeServiceHTTPError as exc:
      # CW-MCP-1-B — auth failures need a distinct signal so agents
      # can distinguish "wrong token" from "endpoint down" from
      # "note not found".
      if exc.status_code in (401, 403):
        return {
          "contents": [
            {
              "uri": uri,
              "mimeType": "text/plain",
              "text": (
                f"forge-transpile rejected the Bearer token (HTTP {exc.status_code}). "
                f"Rotate FORGE_MCP_BEARER in your MCP client config. "
                f"Cannot fetch {uri}."
              ),
            }
          ],
        }
      return {
        "contents": [
          {
            "uri": uri,
            "mimeType": "text/plain",
            "text": (
              f"Catalog request failed with HTTP {exc.status_code}. "
              f"Cannot fetch {uri}."
            ),
          }
        ],
      }
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  match = next((n for n in notes if n.name == name and n.domain == domain), None)
  if match is None:
    return {
      "contents": [
        {
          "uri": uri,
          "mimeType": "text/plain",
          "text": (
            f"No note found for {uri}. "
            f"Check that domain '{domain}' and name '{name}' both exist."
          ),
        }
      ],
    }

  return {
    "contents": [
      {
        "uri": uri,
        "mimeType": "application/vnd.forge.note+json",
        "text": match.model_dump_json(),
      }
    ],
  }
