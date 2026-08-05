"""`forge_render_viz` — render a pedagogical diagram to a vault file.

CW-forge-render-viz-mcp-tool (drain 2026-07-31-1050).

Sibling to `forge_render_music`. Where that one renders *pitches* to
staff notation via server-side music21/LilyPond, this renders
*parameters* to a physics/notation diagram via pure-Python SVG
generators in forge-transpile's `viz.py`. Wizard reaches for it when
prose alone is weak — a picture of a sinusoid under its envelope, or a
C major triad lit up on a keyboard, carries what a paragraph can't.

Four kinds (v1):
- `sinewave`       — a clean sinusoid. Physics of sound, waveform basics.
- `wave_packet`    — sinusoid × Gaussian envelope, envelope drawn dashed.
- `piano_keyboard` — keyboard segment with optional highlights + labels.
                     Works for chords, intervals, scales, and for
                     teaching black/white layout as a primitive.
- `harmonic_stack` — stacked partials; timbre = fundamental + overtones.

The transport deliberately matches `/render-music`: the endpoint returns
base64 + content_type + sha256 + size_bytes, so the decode-and-write
half below is the same shape as render_music.py's rather than a second
near-identical client branch with its own bugs.

Safety mirrors render_music.py: path traversal rejected, `.svg`
extension enforced, size cap, overwrite refused unless asked, parent
dirs auto-created, atomic write via tmp + rename, 30s HTTP timeout.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from ..forge_service_client import (
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
  _base_url,
)
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_render_viz"

_DEFAULT_MAX_SIZE_MB = 5.0
_HTTP_TIMEOUT_SECONDS = 30.0

# Mirrors viz.VIZ_KINDS in forge-transpile. This list is duplicated
# rather than fetched because it feeds the advertised MCP enum, which
# has to be known at server-construction time — but that means adding a
# kind upstream requires editing here too (drain 2026-08-03-1100).
_ALLOWED_KINDS = (
  "sinewave",
  "sinewave_comparison",
  "loudness_comparison",
  "wave_packet",
  "piano_keyboard",
  "harmonic_stack",
)
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-][A-Za-z0-9_.\- ]*$")


INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["kind", "target_path"],
  "properties": {
    "kind": {
      "type": "string",
      "enum": list(_ALLOWED_KINDS),
      "description": (
        "Diagram kind. sinewave = clean sinusoid (freq, cycles, "
        "amplitude, phase) — spans `cycles` wavelengths, so its shape "
        "does NOT vary with freq. sinewave_comparison = 2+ sinusoids "
        "stacked over one shared time window (freqs, duration_s, "
        "labels, amplitude) — cycle COUNT varies with freq, so this is "
        "the one for 'pitch = frequency' and 'octave = 2x'. "
        "loudness_comparison = 2+ amplitudes at ONE frequency, each lane "
        "annotated with its level in dB against the loudest (freqs held "
        "fixed, height varies) — for 'loudness is logarithmic'. "
        "wave_packet = sinusoid under a Gaussian "
        "envelope (freq, cycles, envelope_center, envelope_width). "
        "piano_keyboard = keyboard segment (range, highlight, labels). "
        "harmonic_stack = stacked partials (fundamental, harmonics, "
        "amplitudes)."
      ),
    },
    "params": {
      "type": "object",
      "description": (
        "Kind-specific parameters. Every one has a default, so `{}` "
        "renders a sensible example of the kind. Unknown keys are "
        "rejected with a 400 naming the bad key."
      ),
    },
    "target_path": {
      "type": "string",
      "description": (
        "Vault-relative destination, must end in .svg "
        "(e.g. 'music_theory/images/a4-sine.svg')."
      ),
    },
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Omit for the first-registered."
      ),
    },
    "overwrite": {
      "type": "boolean",
      "description": "If false (default), refuse when target_path already exists.",
    },
    "max_size_mb": {
      "type": "number",
      "description": "Reject renders larger than this. Default 5.",
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["vault", "path", "absolute_path", "size_bytes", "kind", "sha256"],
  "properties": {
    "git_sha": {
      "type": ["string", "null"],
      "description": (
        "Commit SHA if the vault is git-tracked and the commit "
        "succeeded; null otherwise (the file is written either way)."
      ),
    },
    "vault": {"type": "string"},
    "path": {"type": "string"},
    "absolute_path": {"type": "string"},
    "size_bytes": {"type": "integer"},
    "kind": {"type": "string"},
    "sha256": {"type": "string"},
  },
}

DESCRIPTION = (
  "Render a pedagogical diagram (sinewave, wave_packet, piano_keyboard, "
  "harmonic_stack) to an SVG file in a vault. Sibling to "
  "forge_render_music: that one renders pitches to staff notation, this "
  "renders parameters to a physics/notation figure. Embed the result in "
  "a note with ![[<target_path>]]. Refuses to overwrite unless "
  "overwrite=true. Auto-commits the written file when the vault is "
  "git-tracked and returns git_sha (null if untracked or the commit "
  "failed — the file is written either way)."
)


def _error(text: str, *, vault: str = "", kind: str = "") -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "vault": vault,
      "path": "",
      "absolute_path": "",
      "size_bytes": 0,
      "kind": kind,
      "sha256": "",
      "git_sha": None,
    },
    "isError": True,
  }


def _validate_target_path(path: str) -> Path:
  """Vault-relative, no traversal, no hidden segments, must be .svg."""
  if not path or not path.strip():
    raise ValueError("target_path is empty")
  if "\x00" in path:
    raise ValueError("target_path contains NUL byte")
  p = Path(path)
  if p.is_absolute():
    raise ValueError(f"target_path must be vault-relative, got {path!r}")
  for seg in p.parts:
    if seg in ("..", "."):
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
  if p.suffix.lower() != ".svg":
    raise ValueError(
      f"target_path {path!r} must end in .svg (every viz kind renders SVG), "
      f"got {p.suffix!r}"
    )
  return p


async def run(
  arguments: dict[str, Any],
  bearer: str,
  vault_registry: VaultRegistry,
  client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
  kind = arguments.get("kind")
  params = arguments.get("params") or {}
  target_path = arguments.get("target_path")
  vault_name = arguments.get("vault")
  overwrite = bool(arguments.get("overwrite", False))
  max_size_mb = arguments.get("max_size_mb", _DEFAULT_MAX_SIZE_MB)

  if not isinstance(kind, str) or not kind.strip():
    return _error("Missing required argument: 'kind'.")
  kind = kind.strip()
  if kind not in _ALLOWED_KINDS:
    return _error(
      f"Unsupported kind {kind!r}. Allowed: " + ", ".join(_ALLOWED_KINDS),
      kind=kind,
    )
  if not isinstance(params, dict):
    return _error(
      f"'params' must be an object, got {type(params).__name__}.", kind=kind,
    )
  if not isinstance(target_path, str) or not target_path.strip():
    return _error("Missing required argument: 'target_path'.", kind=kind)
  try:
    max_size_bytes = int(float(max_size_mb) * 1024 * 1024)
    if max_size_bytes <= 0:
      raise ValueError
  except (TypeError, ValueError):
    return _error(
      f"'max_size_mb' must be a positive number, got {max_size_mb!r}.",
      kind=kind,
    )

  try:
    vault_fs = vault_registry.get(vault_name)
  except VaultNotFoundError as exc:
    return _error(str(exc), vault=str(vault_name or ""), kind=kind)
  if vault_name is None or vault_name == "":
    vault_name = vault_registry.names()[0]

  try:
    rel = _validate_target_path(target_path)
  except ValueError as exc:
    return _error(f"Invalid target_path: {exc}", vault=vault_name, kind=kind)

  abs_path = (vault_fs.root / rel).resolve()
  try:
    abs_path.relative_to(vault_fs.root)
  except ValueError:
    return _error(
      f"target_path {target_path!r} resolves outside vault root "
      f"{vault_fs.root}.",
      vault=vault_name, kind=kind,
    )
  if abs_path.exists() and not overwrite:
    return _error(
      f"Refusing to overwrite existing file at {abs_path}. Pass "
      "overwrite=True to replace.",
      vault=vault_name, kind=kind,
    )

  own_client = client is None
  http_client = client if client is not None else httpx.AsyncClient(
    timeout=_HTTP_TIMEOUT_SECONDS,
  )
  try:
    try:
      url = f"{_base_url()}/render-viz"
    except ForgeServiceEndpointMissing as exc:
      return _error(str(exc), vault=vault_name, kind=kind)
    try:
      resp = await http_client.post(
        url,
        json={"kind": kind, "params": params},
        headers={
          "Authorization": f"Bearer {bearer}",
          "Content-Type": "application/json",
        },
      )
    except httpx.TimeoutException:
      return _error(
        f"forge-transpile /render-viz timed out after "
        f"{_HTTP_TIMEOUT_SECONDS:.0f}s.",
        vault=vault_name, kind=kind,
      )
    except httpx.HTTPError as exc:
      return _error(
        f"forge-transpile /render-viz request failed: {exc}",
        vault=vault_name, kind=kind,
      )

    if resp.status_code >= 400:
      # Surface the server's message verbatim — for a 400 it names the
      # offending kind or param, which is exactly what the caller needs.
      try:
        detail = resp.json().get("detail", resp.text)
      except Exception:  # noqa: BLE001 — non-JSON error body
        detail = resp.text
      return _error(
        f"forge-transpile /render-viz returned {resp.status_code}: {detail}",
        vault=vault_name, kind=kind,
      )

    try:
      payload = resp.json()
    except Exception as exc:  # noqa: BLE001
      return _error(
        f"forge-transpile /render-viz returned non-JSON: {exc}",
        vault=vault_name, kind=kind,
      )

    try:
      data = base64.b64decode(payload.get("data_b64", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
      return _error(
        f"forge-transpile /render-viz returned undecodable data_b64: {exc}",
        vault=vault_name, kind=kind,
      )
    if not data:
      return _error(
        "forge-transpile /render-viz returned an empty render.",
        vault=vault_name, kind=kind,
      )
    if len(data) > max_size_bytes:
      return _error(
        f"Render is {len(data)} bytes, over the {max_size_bytes}-byte cap "
        f"(max_size_mb={max_size_mb}).",
        vault=vault_name, kind=kind,
      )

    sha256 = hashlib.sha256(data).hexdigest()
    server_sha = payload.get("sha256")
    if server_sha and server_sha != sha256:
      # Integrity check the raw-string transport couldn't give us.
      return _error(
        f"Checksum mismatch: server said {server_sha}, decoded bytes hash "
        f"to {sha256}. Refusing to write.",
        vault=vault_name, kind=kind,
      )
  finally:
    if own_client:
      await http_client.aclose()

  abs_path.parent.mkdir(parents=True, exist_ok=True)
  tmp = abs_path.with_suffix(abs_path.suffix + ".tmp")
  tmp.write_bytes(data)
  tmp.replace(abs_path)

  rel_path_str = str(abs_path.relative_to(vault_fs.root))
  _commit = vault_fs.auto_commit(
    abs_path, f"forge_render_viz: {kind} -> {rel_path_str}",
    expected_content=abs_path.read_bytes(),
  )
  git_sha = _commit.git_sha
  commit_note = (
    f" Committed {git_sha[:8]}." if git_sha
    else " Not committed (vault not git-tracked)."
  )
  return {
    "content": [{
      "type": "text",
      "text": (
        f"Rendered {kind} to {rel_path_str} in vault {vault_name!r} "
        f"({len(data)} bytes). Embed with ![[{rel_path_str}]].{commit_note}"
      ),
    }],
    "structuredContent": {
      "vault": vault_name,
      "path": rel_path_str,
      "absolute_path": str(abs_path),
      "size_bytes": len(data),
      "kind": kind,
      "sha256": sha256,
      "git_sha": git_sha,
      "foreign_changes_detected": _commit.foreign_changes_detected,
      "foreign_changes_summary": _commit.foreign_changes_summary,
    },
    "isError": False,
  }
