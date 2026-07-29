"""`forge_render_music` — render music21 pitch list to a vault file.

CW-forge-mcp-render-music-tool (drain 2026-07-29-1000).

Authoring-time music rendering for cohort-facing theory notes. Wizard
sends pitch list + target vault path; this tool POSTs forge-transpile's
/render-music endpoint (which uses server-side music21 to serialize
the pitches to MIDI bytes), decodes the returned base64, and writes
the .mid file into the target vault at `target_path` atomically.

MVP: format='midi' only. format='svg' returns a clear "pending LilyPond
binary deployment" error so the deferral is visible at the tool
surface too.

Safety:
- Path traversal: reject `..`, absolute paths, hidden segments.
- Extension check: format='midi' requires `.mid` or `.midi`; format='svg'
  requires `.svg`.
- Size cap max_size_mb (default 5) enforced after download.
- Overwrite refused unless overwrite=True.
- Parent directories auto-created.
- 30-second HTTP timeout to forge-transpile.

Reuses the atomic-write pattern from `save_image_from_url`.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

from ..forge_service_client import (
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
  _base_url,
)
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_render_music"

_DEFAULT_MAX_SIZE_MB = 5.0
_HTTP_TIMEOUT_SECONDS = 30.0

_ALLOWED_FORMATS = {"midi", "svg"}
_FORMAT_EXTENSIONS = {
  "midi": {".mid", ".midi"},
  "svg": {".svg"},
}

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-][A-Za-z0-9_.\- ]*$")


INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["pitches", "target_path"],
  "properties": {
    "pitches": {
      "type": "array",
      "items": {"type": "string"},
      "description": (
        "music21 pitch names, e.g. ['C4','D4','E4','F4','G4','A4','B4','C5']. "
        "Non-empty."
      ),
    },
    "target_path": {
      "type": "string",
      "description": (
        "Vault-relative path (e.g. 'music_theory/audio/c_major_scale.mid'). "
        "Extension must match format (.mid/.midi for midi, .svg for svg). "
        "No absolute paths, no '..' segments, no hidden segments."
      ),
    },
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Omit for the first-registered."
      ),
    },
    "format": {
      "type": "string",
      "description": "'midi' (MVP) or 'svg' (deferred — LilyPond binary pending). Default 'midi'.",
    },
    "tempo_bpm": {
      "type": "integer",
      "description": "Metronome mark BPM for MIDI (ignored for svg). Default 90.",
    },
    "duration_quarters": {
      "type": "number",
      "description": "Duration per pitch in quarter notes. Default 1.0.",
    },
    "overwrite": {
      "type": "boolean",
      "description": "If false (default), refuse when target_path already exists.",
    },
    "max_size_mb": {
      "type": "number",
      "description": "Max downloaded size in MB. Default 5.",
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": [
    "vault", "path", "absolute_path", "size_bytes",
    "sha256", "format", "pitch_count",
  ],
  "properties": {
    "vault": {"type": "string"},
    "path": {"type": "string"},
    "absolute_path": {"type": "string"},
    "size_bytes": {"type": "integer"},
    "sha256": {"type": "string"},
    "format": {"type": "string"},
    "pitch_count": {"type": "integer"},
  },
}

DESCRIPTION = (
  "Render a music21 pitch list to an audio/notation file and save it "
  "into a vault at target_path. MVP MIDI-only (format='midi'); "
  "format='svg' returns a clear 'not yet implemented' error pending "
  "LilyPond binary deployment. Path traversal / hidden segments / "
  "extension mismatch / overwrite / oversized downloads are all "
  "rejected. Wizard uses this to embed audio into vanilla theory "
  "notes via ![[<path>]] wikilinks. Cohort clicks the embed in "
  "Obsidian to audition through their system MIDI player."
)


def _error(text: str, *, vault: str = "", fmt: str = "") -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "path": "",
      "absolute_path": "",
      "size_bytes": 0,
      "sha256": "",
      "format": fmt,
      "pitch_count": 0,
    },
    "isError": True,
  }


def _validate_target_path(path: str, fmt: str) -> Path:
  """Return validated relative Path or raise ValueError with a clear message."""
  if not path or not path.strip():
    raise ValueError("target_path is empty")
  if "\x00" in path:
    raise ValueError("target_path contains NUL byte")
  if path.startswith("/"):
    raise ValueError(f"target_path must be vault-relative, got {path!r}")
  for seg in path.split("/"):
    if seg in ("", ".", ".."):
      raise ValueError(
        f"target_path {path!r} contains a forbidden segment {seg!r}"
      )
    if seg.startswith("."):
      raise ValueError(
        f"target_path {path!r} refers to a hidden segment {seg!r}"
      )
    if not _SEGMENT_RE.match(seg):
      raise ValueError(
        f"target_path {path!r} contains a segment with unsupported "
        f"characters: {seg!r}"
      )
  rel = Path(path)
  ext = rel.suffix.lower()
  allowed_exts = _FORMAT_EXTENSIONS[fmt]
  if ext not in allowed_exts:
    raise ValueError(
      f"target_path {path!r} extension {ext!r} does not match format "
      f"{fmt!r}. Allowed extensions for this format: "
      + ", ".join(sorted(allowed_exts))
    )
  return rel


async def run(
  arguments: dict[str, Any],
  bearer: str,
  vault_registry: VaultRegistry,
  *,
  client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
  pitches = arguments.get("pitches")
  target_path = arguments.get("target_path")
  vault_name = arguments.get("vault")
  fmt_raw = arguments.get("format", "midi")
  tempo_bpm = arguments.get("tempo_bpm", 90)
  duration_quarters = arguments.get("duration_quarters", 1.0)
  overwrite = bool(arguments.get("overwrite", False))
  max_size_mb_raw = arguments.get("max_size_mb", _DEFAULT_MAX_SIZE_MB)

  # Argument validation.
  if not isinstance(pitches, list) or not pitches:
    return _error("'pitches' must be a non-empty list of music21 pitch names.")
  if not isinstance(target_path, str) or not target_path.strip():
    return _error("Missing required argument: 'target_path'.")
  fmt = str(fmt_raw).lower().strip()
  if fmt not in _ALLOWED_FORMATS:
    return _error(
      f"'format' {fmt_raw!r} is not supported. Allowed: "
      + ", ".join(sorted(_ALLOWED_FORMATS)),
      fmt=fmt,
    )
  if fmt == "svg":
    return _error(
      "format='svg' is not yet implemented — pending LilyPond binary "
      "deployment. Use format='midi' for MVP.",
      fmt=fmt,
    )
  try:
    max_size_mb = float(max_size_mb_raw)
    if max_size_mb <= 0:
      raise ValueError("must be positive")
  except (TypeError, ValueError):
    return _error(
      f"'max_size_mb' must be a positive number, got {max_size_mb_raw!r}.",
      fmt=fmt,
    )
  max_size_bytes = int(max_size_mb * 1024 * 1024)

  # Vault resolve.
  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), fmt=fmt)
  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  # Path validation (extension check enforces format match).
  try:
    rel = _validate_target_path(target_path, fmt)
  except ValueError as exc:
    return _error(f"Invalid target_path: {exc}", vault=vault_name, fmt=fmt)

  abs_path = (vault_fs.root / rel).resolve()
  try:
    abs_path.relative_to(vault_fs.root)
  except ValueError:
    return _error(
      f"target_path {target_path!r} resolves outside vault root {vault_fs.root}.",
      vault=vault_name, fmt=fmt,
    )

  if abs_path.exists() and not overwrite:
    return _error(
      f"Refusing to overwrite existing file at {abs_path}. Pass "
      "overwrite=True to replace.",
      vault=vault_name, fmt=fmt,
    )

  # POST /render-music.
  own_client = client is None
  http_client = client if client is not None else httpx.AsyncClient(
    timeout=_HTTP_TIMEOUT_SECONDS,
  )
  try:
    url = f"{_base_url()}/render-music"
    body_payload = {
      "pitches": pitches,
      "format": fmt,
      "tempo_bpm": tempo_bpm,
      "duration_quarters": duration_quarters,
    }
    try:
      resp = await http_client.post(
        url,
        json=body_payload,
        headers={
          "Authorization": f"Bearer {bearer}",
          "Content-Type": "application/json",
        },
      )
    except httpx.TimeoutException:
      return _error(
        f"forge-transpile /render-music timed out after {_HTTP_TIMEOUT_SECONDS:.0f}s.",
        vault=vault_name, fmt=fmt,
      )
    except httpx.HTTPError as exc:
      return _error(
        f"forge-transpile /render-music HTTP failed: {exc}",
        vault=vault_name, fmt=fmt,
      )

    if resp.status_code == 404:
      return _error(
        str(ForgeServiceEndpointMissing("/render-music", _base_url())),
        vault=vault_name, fmt=fmt,
      )
    if resp.status_code == 501:
      return _error(
        f"forge-transpile /render-music returned 501: "
        f"{resp.json().get('detail', '')[:200]}",
        vault=vault_name, fmt=fmt,
      )
    if resp.status_code == 400:
      # music21 validation surfaced as HTTP 400 with structured detail.
      return _error(
        f"Render failed (HTTP 400): {resp.json().get('detail', '')[:400]}",
        vault=vault_name, fmt=fmt,
      )
    if resp.status_code == 401 or resp.status_code == 403:
      return _error(
        f"forge-transpile auth failed ({resp.status_code}). Check FORGE_MCP_BEARER.",
        vault=vault_name, fmt=fmt,
      )
    if resp.status_code >= 400:
      raise ForgeServiceHTTPError(resp.status_code, url, resp.text)

    payload = resp.json()
    data_b64 = payload.get("data_b64", "")
    try:
      data = base64.b64decode(data_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
      return _error(
        f"forge-transpile returned invalid base64 data: {exc}",
        vault=vault_name, fmt=fmt,
      )
  finally:
    if own_client:
      await http_client.aclose()

  if len(data) > max_size_bytes:
    return _error(
      f"Rendered {len(data)} bytes exceeds max_size_mb={max_size_mb} "
      f"({max_size_bytes} bytes). Raise max_size_mb to accept larger files.",
      vault=vault_name, fmt=fmt,
    )

  # Verify sha256 matches what the server computed (defense against
  # corruption in transit or base64 tampering).
  local_sha = hashlib.sha256(data).hexdigest()
  server_sha = payload.get("sha256")
  if server_sha and server_sha != local_sha:
    return _error(
      f"SHA-256 mismatch: server {server_sha}, local {local_sha}. Aborting.",
      vault=vault_name, fmt=fmt,
    )

  # Atomic write.
  abs_path.parent.mkdir(parents=True, exist_ok=True)
  tmp = abs_path.with_suffix(abs_path.suffix + ".tmp")
  tmp.write_bytes(data)
  tmp.replace(abs_path)

  rel_path_str = str(abs_path.relative_to(vault_fs.root))
  structured = {
    "vault": vault_name,
    "path": rel_path_str,
    "absolute_path": str(abs_path),
    "size_bytes": len(data),
    "sha256": local_sha,
    "format": fmt,
    "pitch_count": len(pitches),
  }
  return {
    "content": [{
      "type": "text",
      "text": (
        f"Rendered {len(pitches)} pitches to {rel_path_str!r} "
        f"({len(data)} bytes, format={fmt}, sha256={local_sha[:12]}) in "
        f"vault {vault_name!r}."
      ),
    }],
    "structuredContent": structured,
    "isError": False,
  }
