"""`forge_write_message` — cross-cowork messaging channel for wizard + peers.

CW-forge-mcp-write-message-tool (drain 2026-07-29-0900).

Wizard (blind lane) can't write files to `~/projects/forge-moda-bootstrap/
messages/**`, so wizard → forge-core and wizard → forge-reviewer
communication requires driver copy-paste. This tool gives wizard a
scoped write channel matching the existing convention (CCQA writes to
`messages/pending/to-forge-core/from-ccqa/`, etc.). Symmetric access — any
cowork with forge-mcp can now message any whitelisted peer.

Path: writes ONLY under `$FORGE_MCP_MESSAGES_ROOT`, default
`~/projects/forge-moda-bootstrap/messages/`. Absolute-path prefix check
prevents escape. Filename: `YYYY-MM-DD-HHMM-<slug>.md` with `-2`, `-3`,
… suffix on collision.

Safety:
- Whitelist `to` and `from` — reject anything outside the known cowork
  set. Prevents wizard from writing to arbitrary paths + prevents
  impersonation.
- Sanitize `slug` — reject slashes, `..`, hidden segments.
- Refuse body > `max_size_kb` (default 100 KB).
- Refuse writes that resolve outside `$FORGE_MCP_MESSAGES_ROOT`.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_NAME = "forge_write_message"

_DEFAULT_MAX_SIZE_KB = 100.0
_DEFAULT_MESSAGES_ROOT = "~/projects/forge-moda-bootstrap/messages"

# Whitelists — extend by adding to these sets. Kept lowercase per repo
# convention (directory names are all lowercase with dashes).
_ALLOWED_TARGETS = {
  "forge-core", "forge-reviewer", "ccqa",
  "ccdocs", "forge-doc", "forge-moda", "forge-music",
}
_ALLOWED_SOURCES = {
  "wizard", "forge-core", "forge-reviewer", "ccqa",
  "ccdocs", "forge-doc",
}

# Matches vault_fs._NOTE_ID_SEGMENT — same char allowlist for slug
# segments so filenames follow the repo's overall naming policy.
_SLUG_SAFE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_ -]*$")


INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["to", "body"],
  "properties": {
    "to": {
      "type": "string",
      "description": (
        "Target cowork. Must be one of: "
        + ", ".join(sorted(_ALLOWED_TARGETS)) + "."
      ),
    },
    "body": {
      "type": "string",
      "description": "Markdown body of the message.",
    },
    "from": {
      "type": "string",
      "description": (
        "Source cowork. Defaults to 'wizard'. Must be one of: "
        + ", ".join(sorted(_ALLOWED_SOURCES)) + "."
      ),
    },
    "slug": {
      "type": "string",
      "description": (
        "Optional short slug for the filename. Auto-generated from body "
        "if absent. Must not contain slashes, '..', or hidden segments."
      ),
    },
    "max_size_kb": {
      "type": "number",
      "description": "Max body size in KB. Default 100.",
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["path", "size_bytes", "sha256", "to", "from", "timestamp_utc"],
  "properties": {
    "path": {"type": "string"},
    "size_bytes": {"type": "integer"},
    "sha256": {"type": "string"},
    "to": {"type": "string"},
    "from": {"type": "string"},
    "timestamp_utc": {"type": "string"},
  },
}

DESCRIPTION = (
  "Write a markdown message from one cowork to another under "
  "$FORGE_MCP_MESSAGES_ROOT (default ~/projects/forge-moda-bootstrap/"
  "messages/). Filename is YYYY-MM-DD-HHMM-<slug>.md; slug auto-derived "
  "from body first-8-words if absent. `to` and `from` are whitelisted "
  "cowork names. Refuses body > max_size_kb (default 100). Path escape "
  "attempts and unwhitelisted names are rejected. Idempotent on collision "
  "via -2, -3, ... suffix."
)


def _error(text: str, *, to: str = "", from_: str = "") -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "path": "",
      "size_bytes": 0,
      "sha256": "",
      "to": to,
      "from": from_,
      "timestamp_utc": "",
    },
    "isError": True,
  }


def _slug_from_body(body: str) -> str:
  """First 5-8 words → lowercase kebab, capped at 60 chars.

  Strips markdown-ish punctuation before splitting. Empty body → "message".
  """
  # Strip common markdown syntax so slug reads cleanly.
  cleaned = re.sub(r"[#*_`>\[\]()!\-]", " ", body)
  cleaned = re.sub(r"\s+", " ", cleaned).strip()
  words = cleaned.split(" ")
  words = [w for w in words if w]  # drop empties
  if not words:
    return "message"
  # First 8 words, lowercased, non-alphanumeric → dash.
  slug_words = [re.sub(r"[^a-z0-9]+", "-", w.lower()).strip("-") for w in words[:8]]
  slug_words = [w for w in slug_words if w]
  if not slug_words:
    return "message"
  slug = "-".join(slug_words)[:60].rstrip("-")
  return slug or "message"


def _validate_slug(slug: str) -> None:
  """Raise ValueError with a clear message if slug is unsafe."""
  if not slug or not slug.strip():
    raise ValueError("slug is empty")
  if "/" in slug or "\\" in slug:
    raise ValueError(f"slug {slug!r} contains a path separator")
  if ".." in slug:
    raise ValueError(f"slug {slug!r} contains '..' (path traversal)")
  if slug.startswith("."):
    raise ValueError(f"slug {slug!r} starts with '.' (hidden segment)")
  if "\x00" in slug:
    raise ValueError("slug contains NUL byte")
  if not _SLUG_SAFE.match(slug):
    raise ValueError(
      f"slug {slug!r} contains characters outside the allowlist "
      "([A-Za-z0-9_-] with spaces internal only)"
    )


def _messages_root() -> Path:
  raw = os.environ.get("FORGE_MCP_MESSAGES_ROOT", _DEFAULT_MESSAGES_ROOT)
  return Path(raw).expanduser().resolve()


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
) -> dict[str, Any]:
  to = arguments.get("to")
  body = arguments.get("body")
  from_ = arguments.get("from") or arguments.get("from_") or "wizard"
  slug = arguments.get("slug")
  max_size_kb_raw = arguments.get("max_size_kb", _DEFAULT_MAX_SIZE_KB)

  if not isinstance(to, str) or not to.strip():
    return _error("Missing required argument: 'to'.")
  if not isinstance(body, str):
    return _error("Missing required argument: 'body' (markdown string).", to=to)
  if not isinstance(from_, str) or not from_.strip():
    return _error("'from' must be a non-empty string.", to=to)
  try:
    max_size_kb = float(max_size_kb_raw)
    if max_size_kb <= 0:
      raise ValueError("must be positive")
  except (TypeError, ValueError):
    return _error(
      f"'max_size_kb' must be a positive number, got {max_size_kb_raw!r}.",
      to=to, from_=from_,
    )

  to_norm = to.strip().lower()
  from_norm = from_.strip().lower()

  if to_norm not in _ALLOWED_TARGETS:
    return _error(
      f"'to' value {to!r} is not in the allowed target set. Allowed: "
      + ", ".join(sorted(_ALLOWED_TARGETS)) + ".",
      to=to, from_=from_,
    )
  if from_norm not in _ALLOWED_SOURCES:
    return _error(
      f"'from' value {from_!r} is not in the allowed source set. Allowed: "
      + ", ".join(sorted(_ALLOWED_SOURCES)) + ".",
      to=to, from_=from_,
    )

  max_size_bytes = int(max_size_kb * 1024)
  body_bytes = body.encode("utf-8")
  if len(body_bytes) > max_size_bytes:
    return _error(
      f"Body size {len(body_bytes)} bytes exceeds max_size_kb={max_size_kb} "
      f"({max_size_bytes} bytes). Raise max_size_kb to override.",
      to=to, from_=from_,
    )

  # Slug: caller-supplied or auto-derived.
  if slug is None or slug == "":
    slug_final = _slug_from_body(body)
  else:
    try:
      _validate_slug(slug)
    except ValueError as exc:
      return _error(f"Invalid slug: {exc}", to=to, from_=from_)
    slug_final = slug

  # Timestamp.
  now = datetime.now(timezone.utc)
  date_stamp = now.strftime("%Y-%m-%d-%H%M")
  iso_stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

  root = _messages_root()
  target_dir = root / "pending" / f"to-{to_norm}" / f"from-{from_norm}"

  # Sanity: target_dir must live under root.
  try:
    target_dir.resolve().relative_to(root)
  except ValueError:
    return _error(
      f"Target directory resolves outside messages root {root}. Refusing.",
      to=to, from_=from_,
    )

  target_dir.mkdir(parents=True, exist_ok=True)

  # Collision suffix: -2, -3, ...
  base_name = f"{date_stamp}-{slug_final}.md"
  target_path = target_dir / base_name
  suffix = 2
  while target_path.exists():
    target_path = target_dir / f"{date_stamp}-{slug_final}-{suffix}.md"
    suffix += 1
    if suffix > 100:
      return _error(
        f"Refusing to create >100 collision-suffixed files at {target_dir}.",
        to=to, from_=from_,
      )

  # Atomic write via .tmp + rename.
  tmp = target_path.with_suffix(target_path.suffix + ".tmp")
  tmp.write_bytes(body_bytes)
  tmp.replace(target_path)

  sha256 = hashlib.sha256(body_bytes).hexdigest()
  structured = {
    "path": str(target_path),
    "size_bytes": len(body_bytes),
    "sha256": sha256,
    "to": to_norm,
    "from": from_norm,
    "timestamp_utc": iso_stamp,
  }
  return {
    "content": [{
      "type": "text",
      "text": (
        f"Wrote {len(body_bytes)} bytes to {target_path} "
        f"(to={to_norm}, from={from_norm}, sha256={sha256[:12]})."
      ),
    }],
    "structuredContent": structured,
    "isError": False,
  }
