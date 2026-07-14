"""`forge-artifact:///{run_id}/{artifact_name}` — on-demand binary fetch.

CW-MCP-2-B. Backing store is forge-transpile's
`GET /run/{run_id}/artifact/{name}` — which streams the file with the
recorded mime type. This module owns URI parsing + fetching.

Binaries return base64-encoded via MCP's `contents[].blob` field so
the client can render them (music21 XML, MIDI, PNGs).
"""
from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlparse

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceHTTPError,
)

SCHEME = "forge-artifact"


def build_forge_artifact_uri(run_id: str, artifact_name: str) -> str:
  if "/" in run_id:
    raise ValueError(f"run_id may not contain '/': {run_id!r}")
  if "/" in artifact_name:
    raise ValueError(f"artifact_name may not contain '/': {artifact_name!r}")
  return f"{SCHEME}:///{run_id}/{artifact_name}"


def parse_forge_artifact_uri(uri: str) -> tuple[str, str]:
  """Extract `(run_id, artifact_name)` from a forge-artifact URI.

  Mirrors the parser structure of `forge-note:///{domain}/{name}` for
  consistency — see resources/note_uri.py.
  """
  if not isinstance(uri, str):
    raise ValueError(f"URI must be a string, got {type(uri).__name__}")
  parsed = urlparse(uri)
  if parsed.scheme != SCHEME:
    raise ValueError(f"expected {SCHEME!r} scheme, got {parsed.scheme!r} in {uri!r}")
  if parsed.netloc:
    raise ValueError(
      f"forge-artifact URIs must have empty authority (triple slash), got {uri!r}"
    )
  path = parsed.path
  if not path.startswith("/"):
    raise ValueError(f"forge-artifact URI path must start with '/', got {uri!r}")
  parts = path[1:].split("/")
  if len(parts) != 2:
    raise ValueError(
      f"forge-artifact URI must have exactly two path segments, got {uri!r}"
    )
  run_id, artifact_name = parts
  if not run_id or not artifact_name:
    raise ValueError(f"forge-artifact URI segments must be non-empty, got {uri!r}")
  return run_id, artifact_name


async def read_artifact_resource(
  uri: str,
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Fetch a single artifact by URI. Returns MCP resource-read shape.

  Text-ish mime types (text/plain, application/json, XML variants) go
  through the `text` field; everything else base64s into `blob`. This
  matches how MCP clients handle inline resource content.
  """
  run_id, artifact_name = parse_forge_artifact_uri(uri)

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      body, mime = await client.fetch_artifact(
        run_id=run_id, artifact_name=artifact_name, bearer=bearer,
      )
    except ForgeServiceHTTPError as exc:
      return {
        "contents": [
          {
            "uri": uri,
            "mimeType": "text/plain",
            "text": (
              f"Artifact fetch failed with HTTP {exc.status_code}. "
              f"Run may have expired, or the token doesn't own it."
            ),
          }
        ]
      }
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  # Route by mime type. Prefer text-encoded for anything the MCP client
  # can render inline; base64 blob for binaries.
  if _is_text_mime(mime):
    return {
      "contents": [
        {
          "uri": uri,
          "mimeType": mime,
          "text": body.decode("utf-8", errors="replace"),
        }
      ]
    }
  return {
    "contents": [
      {
        "uri": uri,
        "mimeType": mime,
        "blob": base64.b64encode(body).decode("ascii"),
      }
    ]
  }


def _is_text_mime(mime: str) -> bool:
  """Best-effort text/binary discrimination for resource-read shape.

  Text: `text/*` + JSON + XML variants (MusicXML is
  `application/vnd.recordare.musicxml+xml` which IS UTF-8 text).
  """
  primary = mime.split(";", 1)[0].strip().lower()
  if primary.startswith("text/"):
    return True
  if primary in ("application/json", "application/xml"):
    return True
  if primary.endswith("+xml") or primary.endswith("+json"):
    return True
  return False
