"""CW-forge-mcp-render-music-tool (drain 2026-07-29-1000)."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.tools import render_music
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


_C_MAJOR = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]

# Minimal valid MIDI header: MThd + length + format=0 + tracks=1 + tpq=480
_MIDI_HEADER = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
# Minimal track: MTrk + length + end-of-track meta event
_MIDI_TRACK = b"MTrk\x00\x00\x00\x04\x00\xff\x2f\x00"
_FAKE_MIDI = _MIDI_HEADER + _MIDI_TRACK  # ~27 bytes; MThd magic, valid enough for MVP test


def _fake_transpile_response(data: bytes = _FAKE_MIDI) -> dict:
  return {
    "format": "midi",
    "content_type": "audio/midi",
    "size_bytes": len(data),
    "sha256": hashlib.sha256(data).hexdigest(),
    "data_b64": base64.b64encode(data).decode("ascii"),
  }


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture(autouse=True)
def _transpile_url(monkeypatch):
  monkeypatch.setenv("FORGE_TRANSPILE_URL", "https://transpile.test")


@pytest.mark.asyncio
async def test_renders_midi_scale_and_writes_to_vault(single_vault_registry: VaultRegistry):
  """Acceptance #2: happy path — POSTs /render-music, decodes base64,
  writes valid .mid to vault."""
  async with respx.mock(base_url="https://transpile.test") as mock:
    mock.post("/render-music").mock(
      return_value=httpx.Response(200, json=_fake_transpile_response()),
    )
    async with httpx.AsyncClient() as client:
      result = await render_music.run(
        arguments={
          "pitches": _C_MAJOR,
          "target_path": "music_theory/audio/c_major.mid",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result
  vault_fs = single_vault_registry.get()
  saved = vault_fs.root / "music_theory" / "audio" / "c_major.mid"
  assert saved.is_file()
  assert saved.read_bytes()[:4] == b"MThd"
  structured = result["structuredContent"]
  assert structured["vault"] == "default"
  assert structured["format"] == "midi"
  assert structured["pitch_count"] == 8
  assert structured["size_bytes"] > 0
  assert structured["sha256"] == hashlib.sha256(_FAKE_MIDI).hexdigest()


@pytest.mark.asyncio
async def test_svg_passes_through_to_transpile(single_vault_registry: VaultRegistry):
  """CW-forge-transpile-render-music-svg-format (drain 2026-07-29-1500):
  format='svg' no longer short-circuits at the tool layer. It now POSTs
  /render-music and expects real SVG bytes. This test mocks the transpile
  side (real LilyPond invocation is verified end-to-end post-deploy on
  EC2 where the binary is installed via deploy/user-data.sh)."""
  fake_svg = b"<?xml version=\"1.0\"?><svg xmlns=\"http://www.w3.org/2000/svg\"><g/></svg>"
  fake_response = {
    "format": "svg",
    "content_type": "image/svg+xml",
    "size_bytes": len(fake_svg),
    "sha256": hashlib.sha256(fake_svg).hexdigest(),
    "data_b64": base64.b64encode(fake_svg).decode("ascii"),
  }
  async with respx.mock(base_url="https://transpile.test") as mock:
    mock.post("/render-music").mock(
      return_value=httpx.Response(200, json=fake_response),
    )
    async with httpx.AsyncClient() as client:
      result = await render_music.run(
        arguments={
          "pitches": _C_MAJOR,
          "target_path": "music_theory/svg/c_major.svg",
          "format": "svg",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is False, result
  vault_fs = single_vault_registry.get()
  saved = vault_fs.root / "music_theory" / "svg" / "c_major.svg"
  assert saved.is_file()
  head = saved.read_bytes()[:5].decode("utf-8", errors="replace").lower()
  assert head.startswith("<?xml") or head.startswith("<svg"), (
    f"expected SVG magic (<?xml or <svg), got {head!r}"
  )
  assert result["structuredContent"]["format"] == "svg"


@pytest.mark.asyncio
async def test_svg_binary_missing_returns_targeted_error(single_vault_registry: VaultRegistry):
  """When forge-transpile returns 500 with a LilyPond-binary-missing
  message, wrap it clearly at the tool layer so ops sees the config
  drift."""
  async with respx.mock(base_url="https://transpile.test") as mock:
    mock.post("/render-music").mock(
      return_value=httpx.Response(
        500,
        json={"detail": "LilyPond binary missing or misconfigured in transpile env (format='svg')"},
      ),
    )
    async with httpx.AsyncClient() as client:
      result = await render_music.run(
        arguments={
          "pitches": _C_MAJOR,
          "target_path": "music_theory/svg/x.svg",
          "format": "svg",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is True
  # The tool's existing HTTP-error paths surface transpile's message.
  text = result["content"][0]["text"]
  assert "LilyPond" in text or "500" in text


@pytest.mark.asyncio
async def test_refuses_path_traversal(single_vault_registry: VaultRegistry):
  """Acceptance #5: `../../../etc/x.mid` rejected before any HTTP call."""
  result = await render_music.run(
    arguments={
      "pitches": _C_MAJOR,
      "target_path": "../../../etc/passwd.mid",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "target_path" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_refuses_overwrite_by_default(single_vault_registry: VaultRegistry):
  """Acceptance #6: existing file blocks the write unless overwrite=True."""
  vault_fs = single_vault_registry.get()
  existing = vault_fs.root / "audio" / "scale.mid"
  existing.parent.mkdir(parents=True)
  existing.write_bytes(b"original")
  result = await render_music.run(
    arguments={
      "pitches": _C_MAJOR,
      "target_path": "audio/scale.mid",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert existing.read_bytes() == b"original"
  assert "overwrite=True" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_extension_must_match_format(single_vault_registry: VaultRegistry):
  """`.png` target rejected when format='midi' (must be .mid or .midi)."""
  result = await render_music.run(
    arguments={
      "pitches": _C_MAJOR,
      "target_path": "audio/scale.png",
      "format": "midi",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert ".mid" in result["content"][0]["text"] or "extension" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_missing_pitches_returns_error(single_vault_registry: VaultRegistry):
  """Empty pitches list rejected before HTTP call."""
  result = await render_music.run(
    arguments={"pitches": [], "target_path": "audio/scale.mid"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "non-empty" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_invalid_pitch_from_transpile_wraps_400(single_vault_registry: VaultRegistry):
  """Acceptance #7: forge-transpile 400 with invalid pitch → wrapped as clear tool-level error."""
  async with respx.mock(base_url="https://transpile.test") as mock:
    mock.post("/render-music").mock(
      return_value=httpx.Response(
        400,
        json={"detail": "pitches[1]='not-a-pitch' is not a valid music21 pitch: PitchException: bad string"},
      ),
    )
    async with httpx.AsyncClient() as client:
      result = await render_music.run(
        arguments={
          "pitches": ["C4", "not-a-pitch"],
          "target_path": "audio/scale.mid",
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "HTTP 400" in text
  assert "not-a-pitch" in text


@pytest.mark.asyncio
async def test_size_cap_rejects_oversized(single_vault_registry: VaultRegistry):
  """Rendered bytes over max_size_mb rejected pre-write."""
  huge = _MIDI_HEADER + b"\x00" * (500 * 1024)  # ~500 KB
  async with respx.mock(base_url="https://transpile.test") as mock:
    mock.post("/render-music").mock(
      return_value=httpx.Response(200, json=_fake_transpile_response(data=huge)),
    )
    async with httpx.AsyncClient() as client:
      result = await render_music.run(
        arguments={
          "pitches": _C_MAJOR,
          "target_path": "audio/huge.mid",
          "max_size_mb": 0.1,  # 100 KB — 500 KB payload exceeds
        },
        bearer="tok",
        vault_registry=single_vault_registry,
        client=client,
      )
  assert result["isError"] is True
  assert "max_size_mb" in result["content"][0]["text"]
