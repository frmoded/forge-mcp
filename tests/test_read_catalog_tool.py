"""End-to-end tests for the forge_read_note_catalog tool implementation."""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import read_note_catalog

CATALOG_ONE_NOTE = {
  "notes": [
    {
      "name": "compose_blues",
      "domain": "music",
      "signature": "Call [[compose_blues]] with tempo=int returning MusicXML",
      "short_desc": "12-bar blues.",
      "long_desc": "Compose a standard 12-bar blues.",
      "uri": "forge-note:///music/compose_blues",
    }
  ]
}


@pytest.mark.asyncio
async def test_tool_returns_structured_content_and_summary_text_on_success() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog", params={"domain": "music"}).mock(
      return_value=httpx.Response(200, json=CATALOG_ONE_NOTE)
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      result = await read_note_catalog.run(
        arguments={"domain": "music"},
        bearer="tok",
        client=client,
      )

  assert result["isError"] is False
  assert "structuredContent" in result
  assert result["structuredContent"]["notes"][0]["name"] == "compose_blues"
  assert result["content"][0]["type"] == "text"
  assert "1 note" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tool_returns_empty_catalog_without_error_when_no_domain_filter() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(return_value=httpx.Response(200, json={"notes": []}))
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      result = await read_note_catalog.run(
        arguments={},
        bearer="tok",
        client=client,
      )

  assert result["isError"] is False
  assert result["structuredContent"] == {"notes": []}


@pytest.mark.asyncio
async def test_tool_reports_unknown_domain_as_business_error() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog", params={"domain": "no_such_domain"}).mock(
      return_value=httpx.Response(200, json={"notes": []})
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      result = await read_note_catalog.run(
        arguments={"domain": "no_such_domain"},
        bearer="tok",
        client=client,
      )

  assert result["isError"] is True
  assert "no_such_domain" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tool_translates_missing_forge_endpoint_to_business_error() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(return_value=httpx.Response(404, text="not found"))
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      result = await read_note_catalog.run(
        arguments={},
        bearer="tok",
        client=client,
      )

  assert result["isError"] is True
  assert "unavailable" in result["content"][0]["text"].lower()
  # We surface the specific missing endpoint for driver-facing debug.
  assert "/catalog" in result["content"][0]["text"]
