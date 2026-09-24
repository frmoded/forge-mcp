"""Drain 2026-09-24-2200 — `simultaneous` must survive the FastMCP wrapper.

Root cause of wizard's "chord renders sequentially" report: the decorated
`_forge_render_music` wrapper in server.py accepted `simultaneous` in its
signature (so it was advertised in the tool schema) but never copied it into
the `args` dict handed to `render_music.run`, so `run` always saw the default
False and POSTed `simultaneous: false` to forge-transpile.

Drain 2026-08-14-0380's passthrough tests call `render_music.run` directly with
`simultaneous` already in `arguments`, which is downstream of the drop — they
stayed green while the real MCP path was broken. These tests go through the
registered server tool (the path a real client takes) and capture the body
actually POSTed to /render-music.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.server import _make_server
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

_MIDI = b"MThd" + b"\x00" * 10


class _FakeReqCtx:
  request = None


class _FakeCtx:
  request_context = _FakeReqCtx()


@pytest.fixture
def server(monkeypatch, tmp_path: Path):
  monkeypatch.setenv("FORGE_MCP_BEARER", "test-token-simultaneous")
  root = tmp_path / "vault"
  root.mkdir()
  return _make_server(vault_registry=VaultRegistry({"default": VaultFS(root=root)}))


async def _posted_body(server, **args) -> dict:
  tool = server._tool_manager.get_tool("forge_render_music")
  payload = {
    "format": "midi",
    "size_bytes": len(_MIDI),
    "content_type": "audio/midi",
    "sha256": hashlib.sha256(_MIDI).hexdigest(),
    "data_b64": base64.b64encode(_MIDI).decode("ascii"),
  }
  async with respx.mock(base_url="http://localhost:8000") as mock:
    route = mock.post("/render-music").mock(return_value=httpx.Response(200, json=payload))
    await tool.run(arguments=args, context=_FakeCtx(), convert_result=True)
  assert route.called, "the tool never POSTed to /render-music"
  return json.loads(route.calls.last.request.content)


_BASE = {"pitches": ["C4", "D-4"], "format": "midi", "target_path": "out.mid",
         "tempo_bpm": 60, "duration_quarters": 10}


@pytest.mark.asyncio
async def test_simultaneous_true_reaches_the_service_through_the_server_tool(server):
  body = await _posted_body(server, **_BASE, simultaneous=True)
  assert body["simultaneous"] is True, body


@pytest.mark.asyncio
async def test_simultaneous_false_and_omitted_stay_false(server):
  assert (await _posted_body(server, **_BASE, simultaneous=False))["simultaneous"] is False
  assert (await _posted_body(server, **{**_BASE, "target_path": "o2.mid"}))["simultaneous"] is False
