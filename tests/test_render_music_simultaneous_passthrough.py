"""Drain 2026-08-14-0380 — forge_render_music passes `simultaneous` through.

The flag itself is implemented in forge-transpile; forge-mcp's only job is to
accept it, coerce it to a bool, and put it in the POSTed body. A silent drop
here would look exactly like the feature not working, so these tests capture
the body the tool ACTUALLY sends via the injectable `client` seam rather than
asserting on the arguments we passed in.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from forge_mcp.tools import render_music
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

_MIDI = base64.b64encode(b"MThd" + b"\x00" * 10).decode("ascii")


class _Resp:
  status_code = 200

  def json(self):
    raw = base64.b64decode(_MIDI)
    import hashlib

    return {
      "format": "midi",
      "content_type": "audio/midi",
      "size_bytes": len(raw),
      "sha256": hashlib.sha256(raw).hexdigest(),
      "data_b64": _MIDI,
    }


class _CapturingClient:
  """Stands in for httpx.AsyncClient, recording the POSTed JSON body."""

  def __init__(self) -> None:
    self.body: dict = {}

  async def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
    self.body = dict(json or {})
    return _Resp()


@pytest.fixture
def vault(tmp_path: Path) -> VaultRegistry:
  root = tmp_path / "vault"
  root.mkdir()
  return VaultRegistry({"default": VaultFS(root=root)})


async def _post_body(vault: VaultRegistry, **extra) -> dict:
  client = _CapturingClient()
  args = {
    "pitches": ["C4", "C5"],
    "format": "midi",
    "target_path": "out.mid",
  }
  args.update(extra)
  await render_music.run(
    arguments=args, bearer="tok", vault_registry=vault, client=client
  )
  return client.body


# -- schema / discoverability ----------------------------------------------


def test_simultaneous_is_declared_in_the_input_schema():
  """§4 — a caller must find it without reading source."""
  props = render_music.INPUT_SCHEMA["properties"]
  assert "simultaneous" in props
  assert props["simultaneous"]["type"] == "boolean"


def test_description_mentions_the_flag():
  assert "simultaneous" in render_music.DESCRIPTION.lower()


# -- the actual passthrough -------------------------------------------------


@pytest.mark.asyncio
async def test_true_reaches_the_posted_body(vault: VaultRegistry):
  body = await _post_body(vault, simultaneous=True)
  assert body.get("simultaneous") is True, body


@pytest.mark.asyncio
async def test_false_reaches_the_posted_body(vault: VaultRegistry):
  body = await _post_body(vault, simultaneous=False)
  assert body.get("simultaneous") is False, body


@pytest.mark.asyncio
async def test_omitted_defaults_to_false_in_the_body(vault: VaultRegistry):
  """Omitting it must still send an explicit false, not drop the key."""
  body = await _post_body(vault)
  assert body.get("simultaneous") is False, body


@pytest.mark.asyncio
async def test_truthy_non_bool_is_coerced(vault: VaultRegistry):
  """Mirrors the `overwrite = bool(arguments.get(...))` coercion."""
  body = await _post_body(vault, simultaneous="yes")
  assert body["simultaneous"] is True, body


@pytest.mark.asyncio
async def test_existing_fields_are_unchanged(vault: VaultRegistry):
  """The new key must not disturb the rest of the payload."""
  body = await _post_body(vault, tempo_bpm=120, duration_quarters=2.0)
  assert body["pitches"] == ["C4", "C5"]
  assert body["format"] == "midi"
  assert body["tempo_bpm"] == 120
  assert body["duration_quarters"] == 2.0
