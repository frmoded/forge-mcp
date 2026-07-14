"""Tests for the async forge-transpile HTTP client, using respx to mock."""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import (
  ForgeServiceClient,
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
)

CATALOG_TWO_NOTES = {
  "notes": [
    {
      "name": "compose_blues",
      "domain": "music",
      "signature": "Call [[compose_blues]] with tempo=int returning MusicXML",
      "short_desc": "12-bar blues.",
      "long_desc": "Compose a standard 12-bar blues.",
      "uri": "forge-note:///music/compose_blues",
    },
    {
      "name": "walking_bass",
      "domain": "music",
      "signature": "Call [[walking_bass]] with chord_progression=str returning MIDI",
      "short_desc": "Walking bass line.",
      "long_desc": "Generate a walking bass line over a chord progression.",
      "uri": "forge-note:///music/walking_bass",
    },
  ]
}


@pytest.mark.asyncio
async def test_get_catalog_returns_parsed_notes_on_success() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog", params={"domain": "music"}).mock(
      return_value=httpx.Response(200, json=CATALOG_TWO_NOTES)
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      notes = await client.get_catalog(domain="music", bearer="tok")
    assert len(notes) == 2
    assert notes[0].name == "compose_blues"
    assert notes[1].uri == "forge-note:///music/walking_bass"


@pytest.mark.asyncio
async def test_get_catalog_returns_empty_list_when_catalog_is_empty() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(
      return_value=httpx.Response(200, json={"notes": []})
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      notes = await client.get_catalog(domain=None, bearer="tok")
    assert notes == []


@pytest.mark.asyncio
async def test_get_catalog_raises_endpoint_missing_on_404() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(return_value=httpx.Response(404, text="not found"))
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      with pytest.raises(ForgeServiceEndpointMissing) as exc:
        await client.get_catalog(domain=None, bearer="tok")
    assert "/catalog" in str(exc.value)


@pytest.mark.asyncio
async def test_get_catalog_bubbles_up_500_as_http_error() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(return_value=httpx.Response(500, text="boom"))
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      with pytest.raises(ForgeServiceHTTPError) as exc:
        await client.get_catalog(domain=None, bearer="tok")
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_get_catalog_forwards_bearer_token_in_header() -> None:
  captured_headers: dict[str, str] = {}

  def _capture(request: httpx.Request) -> httpx.Response:
    captured_headers.update(request.headers)
    return httpx.Response(200, json={"notes": []})

  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(side_effect=_capture)
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      await client.get_catalog(domain=None, bearer="my-token-123")

  assert captured_headers.get("authorization") == "Bearer my-token-123"


# `list_vault_notes` was retired in CW-MCP-2-E — the forge-mcp tool
# `forge_read_notes_in_vault` now reads locally via VaultFS (drain
# 2026-07-14-1620). The pre-drain endpoint-missing regression test
# was removed with the client method; the local read path is covered
# by tests/test_read_vault_tool.py.


# -----------------------------------------------------------------------------
# Drain 2670 — response-shape tolerance.
#
# forge-transpile's `/catalog` returns a bare JSON array
# (`response_model=list[NoteEntry]` per drain 1330). CW-MCP-1-A's parser
# assumed a `{"notes": [...]}` wrapper and silently dropped payloads on
# the wire mismatch, surfacing to agents as "No notes found" isError: true.
# The fix widens the client to accept EITHER shape; tests below regression-
# lock both.
# -----------------------------------------------------------------------------


_BARE_ARRAY_ENTRY = {
  "name": "voices_canonical",
  "domain": "music",
  "signature": "Call [[voices_canonical]] with kp, chp",
  "short_desc": "compose canonical voice layout",
  "long_desc": "long",
  "uri": "forge-note:///music/voices_canonical",
}


@pytest.mark.asyncio
async def test_get_catalog_accepts_bare_array_shape() -> None:
  # Current forge-transpile /catalog wire shape.
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(
      return_value=httpx.Response(200, json=[_BARE_ARRAY_ENTRY])
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      notes = await client.get_catalog(domain=None, bearer="tok")
    assert len(notes) == 1
    assert notes[0].name == "voices_canonical"


@pytest.mark.asyncio
async def test_get_catalog_accepts_wrapped_notes_shape() -> None:
  # Future-safe: if forge-transpile ever moves to a wrapped envelope,
  # this test ensures we still parse it.
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(
      return_value=httpx.Response(200, json={"notes": [_BARE_ARRAY_ENTRY]})
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      notes = await client.get_catalog(domain=None, bearer="tok")
    assert len(notes) == 1
    assert notes[0].name == "voices_canonical"


@pytest.mark.asyncio
async def test_get_catalog_returns_empty_on_unexpected_shape() -> None:
  # Defensive: if forge-transpile ever returns garbage, don't crash.
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(
      return_value=httpx.Response(200, json="unexpected-string-payload")
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      notes = await client.get_catalog(domain=None, bearer="tok")
    assert notes == []
