"""Wire-shape regression tests for drain 2026-07-14-1225.

Pre-drain, every tool response's `structuredContent` was nested inside
another `structuredContent` (see drain §2 for the smoke output). Root
cause: the FastMCP-decorated wrappers in `server.py` returned a raw
`{content, structuredContent, isError}` dict, which FastMCP's
`_convert_to_content` re-wrapped as the structured payload.

Post-drain: wrappers return a `mcp.types.CallToolResult` object, which
FastMCP's `FuncMetadata.convert_result` passes through unchanged
(`func_metadata.py` line 114). These tests exercise the FIXED shape:

  * `_to_call_tool_result` correctly maps dict envelope → CallToolResult.
  * Each of the 5 registered tools, invoked through the server's
    FastMCP tool manager, returns a `CallToolResult` whose
    `structuredContent` is the flat outputSchema payload (no nested
    `structuredContent` key).

Together these guard against a future refactor accidentally re-
introducing the double-wrap.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from mcp.types import CallToolResult, TextContent

from forge_mcp.server import _make_server, _to_call_tool_result

# ---------------------------------------------------------------------------
# _to_call_tool_result unit tests
# ---------------------------------------------------------------------------


class TestToCallToolResultHelper:
  def test_maps_success_envelope(self):
    """`run()` success shape → CallToolResult with flat structuredContent."""
    envelope = {
      "content": [{"type": "text", "text": "Compiled OK."}],
      "structuredContent": {"parse_status": "ok", "python_source": "def f(): ..."},
      "isError": False,
    }
    result = _to_call_tool_result(envelope)
    assert isinstance(result, CallToolResult)
    assert result.isError is False
    assert result.structuredContent == {
      "parse_status": "ok",
      "python_source": "def f(): ...",
    }
    # No nested structuredContent (regression guard).
    assert "structuredContent" not in result.structuredContent
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Compiled OK."

  def test_maps_error_envelope(self):
    """`run()` isError=True → CallToolResult with isError=True."""
    envelope = {
      "content": [{"type": "text", "text": "Parse error at line 3: ..."}],
      "structuredContent": {"parse_status": "parse_error"},
      "isError": True,
    }
    result = _to_call_tool_result(envelope)
    assert result.isError is True
    assert result.structuredContent == {"parse_status": "parse_error"}

  def test_handles_missing_optional_fields(self):
    """Missing `isError` defaults to False; missing content is empty list."""
    envelope: dict[str, Any] = {"structuredContent": {"notes": []}}
    result = _to_call_tool_result(envelope)
    assert result.isError is False
    assert result.content == []
    assert result.structuredContent == {"notes": []}


# ---------------------------------------------------------------------------
# End-to-end wire-shape assertions per tool.
#
# We reach into FastMCP's ToolManager to invoke each registered tool
# with `convert_result=True` (the same code path the transport layer
# uses on a real `tools/call` RPC). The returned object is what a
# downstream MCP client would receive.
#
# We do NOT go all the way through the JSON-RPC transport because
# that requires stdio/streamable-http setup — the invariant we're
# testing lives at the tool-return-value layer, not in the transport.
# ---------------------------------------------------------------------------


def _tool(server, name):
  """Fetch a registered tool by name from the FastMCP server's
  ToolManager. FastMCP internal API — used only in tests."""
  # FastMCP exposes `_tool_manager` publicly on the instance; each
  # registered tool lives under its declared name.
  return server._tool_manager.get_tool(name)


class _FakeReqCtx:
  """Enough of `Context.request_context` to satisfy `_bearer_from_context`.
  The bearer is drawn from FORGE_MCP_BEARER since we pass no request."""

  request = None


class _FakeCtx:
  request_context = _FakeReqCtx()


@pytest.fixture
def server(monkeypatch):
  """Fresh server + a FORGE_MCP_BEARER env fallback so tool wrappers
  can extract a bearer without a real Starlette request."""
  monkeypatch.setenv("FORGE_MCP_BEARER", "test-token-wire-shape")
  return _make_server()


@pytest.mark.asyncio
async def test_read_note_catalog_wire_shape_no_double_wrap(server):
  """Drain §5 test #1 — read_note_catalog returns flat structuredContent."""
  tool = _tool(server, "forge_read_note_catalog")
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/catalog").mock(
      return_value=httpx.Response(200, json={"notes": []})
    )
    result = await tool.run(
      arguments={},
      context=_FakeCtx(),
      convert_result=True,
    )
  assert isinstance(result, CallToolResult)
  # Flat shape — the key must be "notes", NOT another "structuredContent".
  assert result.structuredContent is not None
  assert "notes" in result.structuredContent
  assert "structuredContent" not in result.structuredContent


@pytest.mark.asyncio
async def test_compile_recipe_wire_shape_no_double_wrap(server):
  """Drain §5 test #2 — compile_recipe returns flat structuredContent."""
  tool = _tool(server, "forge_compile_recipe")
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.post("/compile").mock(
      return_value=httpx.Response(
        200,
        json={
          "parse_status": "ok",
          "python_source": "def compute(context):\n    return 1",
          "unresolved_slot_count": 0,
          "parse_error": None,
        },
      )
    )
    result = await tool.run(
      arguments={"source": "Return 1.\n"},
      context=_FakeCtx(),
      convert_result=True,
    )
  assert isinstance(result, CallToolResult)
  assert result.structuredContent is not None
  assert result.structuredContent.get("parse_status") == "ok"
  assert "structuredContent" not in result.structuredContent


@pytest.mark.asyncio
async def test_run_recipe_wire_shape_no_double_wrap(server):
  """Drain §5 test #3 — run_recipe returns flat structuredContent."""
  tool = _tool(server, "forge_run_recipe")
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.post("/run").mock(
      return_value=httpx.Response(
        200,
        json={
          "parse_status": "ok",
          "run_id": "abcd1234",
          "exit_code": 0,
          "duration_ms": 42,
          "timed_out": False,
          "stdout_preview": "hello",
          "artifacts": [],
        },
      )
    )
    result = await tool.run(
      arguments={"source": "Return 1.\n"},
      context=_FakeCtx(),
      convert_result=True,
    )
  assert isinstance(result, CallToolResult)
  assert result.structuredContent is not None
  assert result.structuredContent.get("run_id") == "abcd1234"
  assert "structuredContent" not in result.structuredContent


@pytest.mark.asyncio
async def test_get_run_result_wire_shape_no_double_wrap(server):
  """Drain §5 test #4 — get_run_result returns flat structuredContent."""
  tool = _tool(server, "forge_get_run_result")
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/run/abcd1234").mock(
      return_value=httpx.Response(
        200,
        json={
          "run_id": "abcd1234",
          "created_at": 0,
          "expires_at": 0,
          "duration_ms": 10,
          "exit_code": 0,
          "timed_out": False,
          "stdout": "",
          "stderr": "",
          "artifacts": [],
        },
      )
    )
    result = await tool.run(
      arguments={"run_id": "abcd1234"},
      context=_FakeCtx(),
      convert_result=True,
    )
  assert isinstance(result, CallToolResult)
  assert result.structuredContent is not None
  assert result.structuredContent.get("run_id") == "abcd1234"
  assert "structuredContent" not in result.structuredContent


@pytest.mark.asyncio
async def test_read_notes_in_vault_wire_shape_no_double_wrap(server):
  """Drain §5 test #5 — read_notes_in_vault returns flat structuredContent."""
  tool = _tool(server, "forge_read_notes_in_vault")
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/vault/notes").mock(
      return_value=httpx.Response(200, json={"notes": []})
    )
    result = await tool.run(
      arguments={},
      context=_FakeCtx(),
      convert_result=True,
    )
  assert isinstance(result, CallToolResult)
  assert result.structuredContent is not None
  assert "notes" in result.structuredContent
  assert "structuredContent" not in result.structuredContent
