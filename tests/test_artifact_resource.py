"""CW-MCP-2-B — forge-artifact:// resource tests."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.resources.artifact_uri import (
  build_forge_artifact_uri,
  parse_forge_artifact_uri,
  read_artifact_resource,
)

_RUN_ID = "abc123def456abc123def456abc123ef"


def test_parse_forge_artifact_uri_valid() -> None:
  run_id, name = parse_forge_artifact_uri(f"forge-artifact:///{_RUN_ID}/score.xml")
  assert run_id == _RUN_ID
  assert name == "score.xml"


def test_parse_forge_artifact_uri_rejects_missing_segments() -> None:
  with pytest.raises(ValueError):
    parse_forge_artifact_uri("forge-artifact:///onlyone")
  with pytest.raises(ValueError):
    parse_forge_artifact_uri("forge-artifact:///a/b/c")
  with pytest.raises(ValueError):
    parse_forge_artifact_uri("forge-artifact:///")


def test_build_forge_artifact_uri_rejects_slash_in_segments() -> None:
  with pytest.raises(ValueError):
    build_forge_artifact_uri("run/id", "name")
  with pytest.raises(ValueError):
    build_forge_artifact_uri("runid", "path/traversal.xml")


@pytest.mark.asyncio
@respx.mock
async def test_artifact_resource_returns_text_for_text_mime() -> None:
  # MusicXML → text (since `+xml` → text path). Content returned via
  # `text` field, not `blob`.
  respx.get(f"http://localhost:8000/run/{_RUN_ID}/artifact/score.xml").mock(
    return_value=httpx.Response(
      200,
      content=b"<?xml version='1.0'?><score/>",
      headers={"content-type": "application/vnd.recordare.musicxml+xml"},
    )
  )
  uri = build_forge_artifact_uri(_RUN_ID, "score.xml")
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    payload = await read_artifact_resource(uri=uri, bearer="tok", client=client)
  contents = payload["contents"]
  assert len(contents) == 1
  assert contents[0]["mimeType"] == "application/vnd.recordare.musicxml+xml"
  assert "text" in contents[0]
  assert "<score/>" in contents[0]["text"]


@pytest.mark.asyncio
@respx.mock
async def test_artifact_resource_returns_blob_for_binary_mime() -> None:
  # MIDI → binary → base64 in `blob`.
  midi_bytes = b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\x80"
  respx.get(f"http://localhost:8000/run/{_RUN_ID}/artifact/song.mid").mock(
    return_value=httpx.Response(
      200, content=midi_bytes, headers={"content-type": "audio/midi"},
    )
  )
  uri = build_forge_artifact_uri(_RUN_ID, "song.mid")
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    payload = await read_artifact_resource(uri=uri, bearer="tok", client=client)
  contents = payload["contents"]
  assert contents[0]["mimeType"] == "audio/midi"
  assert "blob" in contents[0]
  assert base64.b64decode(contents[0]["blob"]) == midi_bytes


@pytest.mark.asyncio
@respx.mock
async def test_artifact_resource_404_returns_diagnostic_text() -> None:
  # Missing / expired / wrong-user → 404. Resource must still resolve
  # so the agent can read the error.
  respx.get(f"http://localhost:8000/run/{_RUN_ID}/artifact/gone.png").mock(
    return_value=httpx.Response(404, json={"detail": "artifact not found"})
  )
  uri = build_forge_artifact_uri(_RUN_ID, "gone.png")
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    payload = await read_artifact_resource(uri=uri, bearer="tok", client=client)
  contents = payload["contents"]
  assert contents[0]["mimeType"] == "text/plain"
  assert "fetch failed" in contents[0]["text"].lower()
  assert "404" in contents[0]["text"]
