"""CW-MCP-2-B — forge_get_run_result tool tests."""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import get_run_result

_RUN_ID = "abc123def456abc123def456abc123ef"


def _mk_full() -> dict:
  return {
    "run_id": _RUN_ID,
    "created_at": 1234567890,
    "expires_at": 1234567890 + 7 * 86400,
    "duration_ms": 42,
    "exit_code": 0,
    "timed_out": False,
    "stdout": "hello world",
    "stderr": "",
    "artifacts": [
      {
        "name": "score.xml",
        "mime_type": "application/vnd.recordare.musicxml+xml",
        "size_bytes": 100,
        "uri": f"forge-artifact:///{_RUN_ID}/score.xml",
      }
    ],
  }


@pytest.mark.asyncio
@respx.mock
async def test_get_run_result_returns_content_array() -> None:
  respx.get(f"http://localhost:8000/run/{_RUN_ID}").mock(
    return_value=httpx.Response(200, json=_mk_full())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await get_run_result.run(
      arguments={"run_id": _RUN_ID}, bearer="tok", client=client,
    )
  assert result["isError"] is False
  text = result["content"][0]["text"]
  assert "hello world" in text
  assert result["structuredContent"]["run_id"] == _RUN_ID
  assert len(result["structuredContent"]["artifacts"]) == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_run_result_ttl_expired_returns_isError() -> None:
  # forge-transpile returns 404 for expired runs (isolation-preserving —
  # same code as wrong-user).
  respx.get(f"http://localhost:8000/run/{_RUN_ID}").mock(
    return_value=httpx.Response(404, json={"detail": "run not found"})
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await get_run_result.run(
      arguments={"run_id": _RUN_ID}, bearer="tok", client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "not found" in text
  assert "7-day TTL" in text or "expired" in text


@pytest.mark.asyncio
async def test_get_run_result_rejects_missing_run_id() -> None:
  result = await get_run_result.run(
    arguments={}, bearer="tok",
  )
  assert result["isError"] is True
  assert "run_id" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_get_run_result_translates_401() -> None:
  respx.get(f"http://localhost:8000/run/{_RUN_ID}").mock(
    return_value=httpx.Response(401, json={"detail": "invalid"})
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await get_run_result.run(
      arguments={"run_id": _RUN_ID}, bearer="stale", client=client,
    )
  assert result["isError"] is True
  assert "FORGE_MCP_BEARER" in result["content"][0]["text"]
