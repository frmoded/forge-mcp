"""CW-MCP-2-A — forge_compile_recipe tool tests.

Covers the 4 canonical cases the drain requires:
  1. success → isError:false + python_source populated
  2. parse error → isError:true + text contains line/column/message
  3. Bearer forwarded to forge_service_client verbatim
  4. upstream 401 → auth-rejection message
"""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import compile_recipe

_TRIVIAL_RECIPE = "Let hihat = Call [[closed_hihat]].\nReturn hihat.\n"


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_success_returns_python() -> None:
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "ok",
        "python_source": "def compute(context):\n    hihat = closed_hihat()\n    return hihat\n",
        "unresolved_slot_count": 0,
        "parse_error": None,
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": _TRIVIAL_RECIPE},
      bearer="cohort-token",
      client=client,
    )
  assert result["isError"] is False
  assert result["structuredContent"]["parse_status"] == "ok"
  assert "def compute(" in result["structuredContent"]["python_source"]
  assert result["structuredContent"]["unresolved_slot_count"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_parse_error_surfaces_diagnostics() -> None:
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "parse_error",
        "python_source": None,
        "unresolved_slot_count": 0,
        "parse_error": {
          "line": 3,
          "column": 12,
          "message": "unexpected token 'bogus'",
          "expected": "",
        },
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": "Let x === bogus."},
      bearer="cohort-token",
      client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "line 3" in text
  assert "column 12" in text
  assert "unexpected token" in text
  assert result["structuredContent"]["parse_status"] == "parse_error"


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_forwards_bearer_from_context() -> None:
  # Mirror CW-MCP-1-B's test_valid_bearer_forwarded_to_forge_service —
  # asserts the token actually lands on the outbound HTTP request.
  route = respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "ok",
        "python_source": "def compute(context):\n    pass\n",
        "unresolved_slot_count": 0,
        "parse_error": None,
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await compile_recipe.run(
      arguments={"source": _TRIVIAL_RECIPE},
      bearer="specific-token-value",
      client=client,
    )
  assert route.called
  auth_header = route.calls.last.request.headers.get("authorization")
  assert auth_header == "Bearer specific-token-value"


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_translates_401_to_auth_error() -> None:
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(401, json={"detail": "invalid or missing bearer"})
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": _TRIVIAL_RECIPE},
      bearer="stale-token",
      client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  # Same wording pattern as CW-MCP-1-B — rejects + rotate + FORGE_MCP_BEARER.
  assert "401" in text
  assert "FORGE_MCP_BEARER" in text
  assert ("rejected" in text.lower()) or ("invalid" in text.lower())


@pytest.mark.asyncio
async def test_compile_recipe_rejects_empty_source() -> None:
  # Defensive: agent handed us empty source. Bail before hitting HTTP.
  # (Matches the drain's implicit contract: source is required.)
  result = await compile_recipe.run(
    arguments={"source": ""},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "source" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_reports_unresolved_slot_count() -> None:
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "ok",
        "python_source": "def compute(context):\n    lyrics = '<unresolved slot: heartbreak>'\n",
        "unresolved_slot_count": 2,
        "parse_error": None,
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": "Let x = Call [[y]] with lyrics={{ heartbreak }}."},
      bearer="tok",
      client=client,
    )
  assert result["isError"] is False
  assert result["structuredContent"]["unresolved_slot_count"] == 2
  # Text hint mentions the slots so the agent knows to call /resolve-slot.
  assert "2 unresolved" in result["content"][0]["text"]


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_propagates_slots_array() -> None:
  """CW-forge-transpile-compile-recipe-return-slots-array (drain 2026-07-24-1205).

  Forge-transpile's /compile returns `slots: [{slot_id, prose}]`
  alongside `unresolved_slot_count`. MCP's CompileResult MUST
  propagate that field verbatim so wizard can build the
  `resolve_slot={<slot_id>: <python>}` map.
  """
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "ok",
        "python_source": "def compute(context):\n    ...",
        "unresolved_slot_count": 2,
        "slots": [
          {"slot_id": "slot_abcd1234", "prose": "pick a key"},
          {"slot_id": "slot_ef567890", "prose": "blues about heartbreak"},
        ],
        "parse_error": None,
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": "Let a = {{ pick a key }}.\nLet b = {{ blues about heartbreak }}."},
      bearer="tok",
      client=client,
    )
  assert result["isError"] is False
  sc = result["structuredContent"]
  assert sc["unresolved_slot_count"] == 2
  assert "slots" in sc, f"CompileResult missing slots field: {sc!r}"
  assert sc["slots"] == [
    {"slot_id": "slot_abcd1234", "prose": "pick a key"},
    {"slot_id": "slot_ef567890", "prose": "blues about heartbreak"},
  ], f"slots not propagated verbatim: {sc['slots']!r}"
  # Text hint mentions the slot_ids so agents get the wire-loop hint.
  text = result["content"][0]["text"]
  assert "slot_abcd1234" in text and "slot_ef567890" in text, (
    f"expected slot_ids in success text; got: {text!r}"
  )


@pytest.mark.asyncio
@respx.mock
async def test_compile_recipe_slots_defaults_to_empty_when_absent() -> None:
  """Back-compat: if forge-transpile happens to omit `slots` (e.g., a
  pre-drain-1205 build somewhere in the deploy chain), CompileResult
  defaults slots to []. No crash; agent sees `unresolved_slot_count`
  count without per-slot detail."""
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(
      200,
      json={
        "parse_status": "ok",
        "python_source": "def compute(context):\n    return 1",
        "unresolved_slot_count": 0,
        # NOTE: no `slots` key — simulates pre-drain-1205 upstream.
        "parse_error": None,
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": "Return 1.\n"},
      bearer="tok",
      client=client,
    )
  assert result["isError"] is False
  assert result["structuredContent"]["slots"] == []
