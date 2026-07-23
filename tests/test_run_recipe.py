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


# ---------------------------------------------------------------------------
# Drain 2026-07-21-1405 — resolve_slot wire-through (Track B).
#
# forge-transpile drain 1700 shipped an optional `resolve_slot` field on
# POST /run for splicing wizard-resolved Python into E-- `{{ prose }}`
# code slots. This drain teaches forge-mcp to surface + forward that
# field so an agent driving via `forge_run_recipe` can actually reach
# the codepath. QA-1700 is now GREEN (forge-transpile SHA f5dd9f4) so
# the wire-through is safe to enable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_without_resolve_slot_omits_field_from_body() -> None:
  """Back-compat: existing callers keep seeing exactly {source, domains}
  on the wire. `resolve_slot` MUST NOT appear when absent/null/empty."""
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
  assert "resolve_slot" not in sent, (
    f"resolve_slot should be absent when not supplied; body was {sent!r}"
  )


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_with_resolve_slot_passes_it_through() -> None:
  """A well-formed `resolve_slot` lands on the outbound body verbatim."""
  import json as _json

  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={
        "source": _TRIVIAL,
        "resolve_slot": {"slot_0": "return 42"},
      },
      bearer="tok", client=client,
    )
  assert route.called
  sent = _json.loads(route.calls.last.request.content)
  assert sent.get("resolve_slot") == {"slot_0": "return 42"}, (
    f"resolve_slot missing or malformed in body: {sent!r}"
  )


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_with_malformed_resolve_slot_drops_to_none() -> None:
  """Malformed shapes → silently drop to None (matches domains-handling
  pattern at run_recipe.py:132-136). Sanitization is intentional per
  drain §4: agent's inference is capable of producing bad JSON on bad
  days; degrade-to-placeholder beats hard error."""
  import json as _json

  # Case A: not a dict at all.
  route_a = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={"source": _TRIVIAL, "resolve_slot": "not a dict"},
      bearer="tok", client=client,
    )
  assert route_a.called
  sent_a = _json.loads(route_a.calls.last.request.content)
  assert "resolve_slot" not in sent_a, (
    f"non-dict resolve_slot should be dropped; body was {sent_a!r}"
  )
  respx.reset()

  # Case B: dict, but a value is non-string (int).
  route_b = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={"source": _TRIVIAL, "resolve_slot": {"slot_0": 42}},
      bearer="tok", client=client,
    )
  assert route_b.called
  sent_b = _json.loads(route_b.calls.last.request.content)
  assert "resolve_slot" not in sent_b, (
    f"dict-with-non-string-value resolve_slot should be dropped; "
    f"body was {sent_b!r}"
  )


def test_run_recipe_input_schema_declares_resolve_slot() -> None:
  """INPUT_SCHEMA must describe resolve_slot so MCP clients (and any
  schema-filtering dispatcher) know it's a valid input field."""
  props = run_recipe.INPUT_SCHEMA["properties"]
  assert "resolve_slot" in props, (
    f"resolve_slot missing from INPUT_SCHEMA properties: {list(props.keys())}"
  )
  rs = props["resolve_slot"]
  assert rs["type"] == "object"
  # additionalProperties enforces string values in the map.
  assert rs.get("additionalProperties") == {"type": "string"}, (
    f"expected additionalProperties: {{type: string}}, got {rs!r}"
  )
  assert "description" in rs and rs["description"]


@pytest.mark.asyncio
@respx.mock
async def test_forge_service_client_run_recipe_body_shape_direct() -> None:
  """Direct test on ForgeServiceClient.run_recipe (bypasses the tool
  wrapper) — two subcases: without and with resolve_slot. Locks the
  invariant that the client method itself, not just its caller, owns
  the conditional-inclusion behavior."""
  import json as _json

  # Subcase 1: without resolve_slot → body has no such key.
  route1 = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await client.run_recipe(source=_TRIVIAL, bearer="tok")
  assert route1.called
  sent1 = _json.loads(route1.calls.last.request.content)
  assert sent1 == {"source": _TRIVIAL, "domains": ["music"]}, (
    f"without resolve_slot, body must be exactly {{source, domains}}; "
    f"got {sent1!r}"
  )
  respx.reset()

  # Subcase 2: with resolve_slot → body includes the field.
  route2 = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await client.run_recipe(
      source=_TRIVIAL, bearer="tok",
      resolve_slot={"slot_0": "return 42"},
    )
  assert route2.called
  sent2 = _json.loads(route2.calls.last.request.content)
  assert sent2 == {
    "source": _TRIVIAL,
    "domains": ["music"],
    "resolve_slot": {"slot_0": "return 42"},
  }, f"body shape mismatch: {sent2!r}"
