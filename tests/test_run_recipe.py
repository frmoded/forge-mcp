"""CW-MCP-2-B — forge_run_recipe tool tests."""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import run_recipe

_TRIVIAL = "Let hihat = Call [[closed_hihat]].\nReturn hihat.\n"


def _mk_ok(run_id: str = "runid00000000000000000000000000") -> dict:
  return {
    "parse_status": "ok",
    "run_id": run_id,
    "parse_error": None,
    "duration_ms": 42,
    "exit_code": 0,
    "timed_out": False,
    "stdout_preview": "hello",
    "artifacts": [
      {
        "name": "score.xml",
        "mime_type": "application/vnd.recordare.musicxml+xml",
        "size_bytes": 100,
        "uri": f"forge-artifact:///{run_id}/score.xml",
      }
    ],
  }


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_success_returns_run_id() -> None:
  respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await run_recipe.run(
      arguments={"source": _TRIVIAL}, bearer="tok", client=client,
    )
  assert result["isError"] is False
  assert result["structuredContent"]["run_id"].startswith("runid")
  assert "1 artifact" in result["content"][0]["text"]


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_parse_error_surfaces_diagnostics() -> None:
  respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "parse_error",
        "run_id": "",
        "parse_error": {
          "line": 5, "column": 3, "message": "unexpected token", "expected": "",
        },
        "duration_ms": 0, "exit_code": 0, "timed_out": False,
        "stdout_preview": "", "artifacts": [],
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await run_recipe.run(
      arguments={"source": "broken"}, bearer="tok", client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "line 5" in text
  assert "column 3" in text
  assert "unexpected token" in text


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_forwards_bearer() -> None:
  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={"source": _TRIVIAL}, bearer="forwarded-tok", client=client,
    )
  assert route.called
  assert route.calls.last.request.headers["authorization"] == "Bearer forwarded-tok"


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_translates_401() -> None:
  respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(401, json={"detail": "invalid"})
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await run_recipe.run(
      arguments={"source": _TRIVIAL}, bearer="stale-tok", client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "401" in text
  assert "FORGE_MCP_BEARER" in text


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_timeout_surfaces_as_isError() -> None:
  # Timed-out run → isError=true even though the run itself was captured.
  respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "ok",
        "run_id": "abcdef00000000000000000000000000",
        "parse_error": None,
        "duration_ms": 30000,
        "exit_code": 124,
        "timed_out": True,
        "stdout_preview": "",
        "artifacts": [],
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await run_recipe.run(
      arguments={"source": _TRIVIAL}, bearer="tok", client=client,
    )
  assert result["isError"] is True
  assert "TIMED OUT" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Drain 2900 — domains field wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_forwards_domains_field() -> None:
  """Explicit `domains: ["moda"]` lands on the outbound httpx request body."""
  import json as _json

  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={"source": _TRIVIAL, "domains": ["moda"]},
      bearer="tok", client=client,
    )
  assert route.called
  sent = _json.loads(route.calls.last.request.content)
  assert sent["domains"] == ["moda"]


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_defaults_domains_to_music() -> None:
  """Omitting `domains` sends `["music"]` explicitly on the wire so
  forge-transpile's default matches forge-mcp's default at every layer."""
  import json as _json

  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={"source": _TRIVIAL},
      bearer="tok", client=client,
    )
  assert route.called
  sent = _json.loads(route.calls.last.request.content)
  assert sent["domains"] == ["music"]
