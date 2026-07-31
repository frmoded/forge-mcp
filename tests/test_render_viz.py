"""CW-forge-render-viz-mcp-tool (drain 2026-07-31-1050)."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.tools import render_viz
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

_SVG = (
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 120" '
  'width="400" height="120"><polyline points="0,0 1,1" fill="none"/></svg>'
)


def _resp(svg: str = _SVG, kind: str = "sinewave") -> dict:
  data = svg.encode("utf-8")
  return {
    "kind": kind,
    "content_type": "image/svg+xml",
    "size_bytes": len(data),
    "sha256": hashlib.sha256(data).hexdigest(),
    "data_b64": base64.b64encode(data).decode("ascii"),
  }


@pytest.fixture
def registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture(autouse=True)
def _service_url(monkeypatch):
  monkeypatch.setenv("FORGE_TRANSPILE_URL", "https://transpile.test")


def _url() -> str:
  return "https://transpile.test/render-viz"


@pytest.mark.asyncio
@respx.mock
async def test_writes_svg_into_the_vault(registry: VaultRegistry):
  respx.post(_url()).mock(return_value=httpx.Response(200, json=_resp()))
  result = await render_viz.run(
    arguments={"kind": "sinewave", "target_path": "images/a4.svg"},
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is False, result
  sc = result["structuredContent"]
  assert sc["path"] == "images/a4.svg"
  assert sc["kind"] == "sinewave"
  written = Path(sc["absolute_path"])
  assert written.read_text(encoding="utf-8") == _SVG
  # Parent dir auto-created.
  assert written.parent.name == "images"


@pytest.mark.asyncio
@respx.mock
async def test_params_are_forwarded_to_the_endpoint(registry: VaultRegistry):
  route = respx.post(_url()).mock(
    return_value=httpx.Response(200, json=_resp())
  )
  await render_viz.run(
    arguments={
      "kind": "harmonic_stack",
      "params": {"harmonics": [1, 2, 3]},
      "target_path": "h.svg",
    },
    bearer="tok", vault_registry=registry,
  )
  sent = route.calls[0].request
  import json
  body = json.loads(sent.content)
  assert body == {"kind": "harmonic_stack", "params": {"harmonics": [1, 2, 3]}}
  assert sent.headers["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_rejects_unknown_kind_before_any_http(registry: VaultRegistry):
  """No network call for a kind we already know is invalid."""
  result = await render_viz.run(
    arguments={"kind": "spectrogram", "target_path": "x.svg"},
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "spectrogram" in text
  assert "sinewave" in text


@pytest.mark.asyncio
async def test_rejects_non_svg_extension(registry: VaultRegistry):
  result = await render_viz.run(
    arguments={"kind": "sinewave", "target_path": "images/a4.png"},
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is True
  assert ".svg" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_rejects_path_traversal(registry: VaultRegistry):
  for bad in ("../escape.svg", "/abs/path.svg", ".hidden/x.svg"):
    result = await render_viz.run(
      arguments={"kind": "sinewave", "target_path": bad},
      bearer="tok", vault_registry=registry,
    )
    assert result["isError"] is True, bad


@pytest.mark.asyncio
@respx.mock
async def test_refuses_overwrite_unless_asked(registry: VaultRegistry):
  respx.post(_url()).mock(return_value=httpx.Response(200, json=_resp()))
  args = {"kind": "sinewave", "target_path": "a.svg"}
  first = await render_viz.run(
    arguments=args, bearer="tok", vault_registry=registry
  )
  assert first["isError"] is False
  second = await render_viz.run(
    arguments=args, bearer="tok", vault_registry=registry
  )
  assert second["isError"] is True
  assert "overwrite" in second["content"][0]["text"].lower()
  third = await render_viz.run(
    arguments={**args, "overwrite": True}, bearer="tok", vault_registry=registry
  )
  assert third["isError"] is False


@pytest.mark.asyncio
@respx.mock
async def test_surfaces_server_400_verbatim(registry: VaultRegistry):
  """A 400 from the endpoint names the offending param — that message
  is the whole value, so it must reach the caller intact."""
  respx.post(_url()).mock(
    return_value=httpx.Response(
      400, json={"detail": "freq must be a positive finite number, got -1"}
    )
  )
  result = await render_viz.run(
    arguments={
      "kind": "sinewave", "params": {"freq": -1}, "target_path": "a.svg",
    },
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is True
  assert "positive finite number" in result["content"][0]["text"]


@pytest.mark.asyncio
@respx.mock
async def test_checksum_mismatch_refuses_to_write(registry: VaultRegistry):
  """Mirroring /render-music's base64 shape buys an integrity check a
  raw-SVG-string transport couldn't give us. Use it."""
  bad = _resp()
  bad["sha256"] = "0" * 64
  respx.post(_url()).mock(return_value=httpx.Response(200, json=bad))
  result = await render_viz.run(
    arguments={"kind": "sinewave", "target_path": "a.svg"},
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is True
  assert "checksum mismatch" in result["content"][0]["text"].lower()
  assert not (registry.get(None).root / "a.svg").exists()


@pytest.mark.asyncio
@respx.mock
async def test_size_cap_refuses_to_write(registry: VaultRegistry):
  respx.post(_url()).mock(return_value=httpx.Response(200, json=_resp()))
  result = await render_viz.run(
    arguments={
      "kind": "sinewave", "target_path": "a.svg", "max_size_mb": 0.00001,
    },
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is True
  assert "cap" in result["content"][0]["text"].lower()
  assert not (registry.get(None).root / "a.svg").exists()


@pytest.mark.asyncio
@respx.mock
async def test_empty_render_refused(registry: VaultRegistry):
  empty = _resp("")
  respx.post(_url()).mock(return_value=httpx.Response(200, json=empty))
  result = await render_viz.run(
    arguments={"kind": "sinewave", "target_path": "a.svg"},
    bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is True
  assert "empty" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_success_text_tells_wizard_how_to_embed(registry: VaultRegistry):
  """The tool's whole purpose is producing an asset a note embeds, so
  the success message hands back the embed syntax."""
  respx.post(_url()).mock(return_value=httpx.Response(200, json=_resp()))
  result = await render_viz.run(
    arguments={"kind": "sinewave", "target_path": "music_theory/images/a4.svg"},
    bearer="tok", vault_registry=registry,
  )
  assert "![[music_theory/images/a4.svg]]" in result["content"][0]["text"]
