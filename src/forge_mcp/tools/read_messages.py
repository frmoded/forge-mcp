"""`forge_read_messages` + `forge_mark_message_read` — cross-cowork
messaging read half.

CW-forge-mcp-read-message-tool (drain 2026-07-29-1300). Companion to
`forge_write_message` (drain 2026-07-29-0900). Wizard (blind lane) can
now read its own inbox — critique replies from forge-reviewer, requests
from driver, etc. — without driver copy-paste.

Layout mirrors write side + existing CCQA convention:
- Unread: `$FORGE_MCP_MESSAGES_ROOT/pending/to-<to>/from-<from>/<date>-<slug>.md`
- Read:   `$FORGE_MCP_MESSAGES_ROOT/read/to-<to>/from-<from>/<date>-<slug>.md`

Read-tracking chosen: **move from `pending/` to `read/`** (matches existing
`messages/read/to-forge-core/from-*/` convention). `.read` sidecar rejected
because it would divide the state across two files and drift from what
CCQA already does.

Safety:
- Whitelist `to` (same set as write_message).
- Path-scoped reads only under $FORGE_MCP_MESSAGES_ROOT/pending/to-<to>/.
- Body-size cap on return (500 KB default; larger truncated with
  `[TRUNCATED: <N> bytes]` marker).
- `limit` cap at 100 per call.
- `forge_mark_message_read` is idempotent (moving already-in-read is
  a no-op).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_READ_NAME = "forge_read_messages"
TOOL_MARK_NAME = "forge_mark_message_read"

_DEFAULT_MESSAGES_ROOT = "~/projects/forge-moda-bootstrap/messages"
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_DEFAULT_TRUNCATE_KB = 500

# Match write_message's whitelist so read/write are symmetric.
_ALLOWED_TARGETS = {
  "forge-core", "forge-reviewer", "ccqa",
  "ccdocs", "forge-doc", "forge-moda", "forge-music",
  "wizard",  # wizard's own inbox — reviewers reply here
}

# Filename shape written by forge_write_message: YYYY-MM-DD-HHMM-<slug>.md
_FILENAME_RE = re.compile(
  r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<hhmm>\d{4})-(?P<slug>.+)\.md$"
)


READ_INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["to"],
  "properties": {
    "to": {
      "type": "string",
      "description": (
        "Cowork inbox to read (e.g. 'wizard', 'forge-reviewer'). Must be one of: "
        + ", ".join(sorted(_ALLOWED_TARGETS)) + "."
      ),
    },
    "since": {
      "type": "string",
      "description": (
        "Optional ISO timestamp (YYYY-MM-DDTHH:MM:SSZ). Only messages "
        "with timestamps newer than this are returned."
      ),
    },
    "unread_only": {
      "type": "boolean",
      "description": "If true (default), only return unread messages (skip read/).",
    },
    "limit": {
      "type": "integer",
      "description": "Max messages returned per call. Default 20, max 100.",
    },
    "max_body_kb": {
      "type": "number",
      "description": "Truncate message bodies larger than this. Default 500 KB.",
    },
  },
}

READ_OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["messages", "total_count", "returned_count"],
  "properties": {
    "messages": {"type": "array"},
    "total_count": {"type": "integer"},
    "returned_count": {"type": "integer"},
  },
}

READ_DESCRIPTION = (
  "Read messages from a cowork's inbox. Lists files under "
  "$FORGE_MCP_MESSAGES_ROOT/pending/to-<to>/from-<from>/, optionally filtered "
  "by `since` timestamp. `unread_only=True` (default) skips messages "
  "already moved to read/. Sorted newest-first. Body-size cap: 500 KB "
  "per message (larger truncated with marker). limit default 20, max 100. "
  "Whitelisted `to` values only."
)


MARK_INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["path"],
  "properties": {
    "path": {
      "type": "string",
      "description": (
        "Absolute path from a prior forge_read_messages result. Moves the "
        "file to $FORGE_MCP_MESSAGES_ROOT/read/to-<to>/from-<from>/. Idempotent."
      ),
    },
  },
}

MARK_OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["path", "marked_read"],
  "properties": {
    "path": {"type": "string"},
    "marked_read": {"type": "boolean"},
  },
}

MARK_DESCRIPTION = (
  "Mark a message as read by moving it from "
  "$FORGE_MCP_MESSAGES_ROOT/pending/to-<to>/from-<from>/<file>.md to "
  "$FORGE_MCP_MESSAGES_ROOT/read/to-<to>/from-<from>/<file>.md. Idempotent — if the "
  "file is already in read/ (or already moved), returns marked_read=true "
  "without error. Path must be an absolute path under $FORGE_MCP_MESSAGES_ROOT."
)


def _messages_root() -> Path:
  raw = os.environ.get("FORGE_MCP_MESSAGES_ROOT", _DEFAULT_MESSAGES_ROOT)
  return Path(raw).expanduser().resolve()


def _error_read(text: str, *, to: str = "") -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "messages": [],
      "total_count": 0,
      "returned_count": 0,
    },
    "isError": True,
  }


def _error_mark(text: str, *, path: str = "") -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "path": path,
      "marked_read": False,
    },
    "isError": True,
  }


def _parse_timestamp_from_filename(name: str) -> str | None:
  """Extract ISO timestamp from `YYYY-MM-DD-HHMM-<slug>.md` filename.

  Returns e.g. `2026-07-29T14:30:00Z`; None if the filename doesn't match.
  """
  m = _FILENAME_RE.match(name)
  if not m:
    return None
  date = m.group("date")
  hh = m.group("hhmm")[:2]
  mm = m.group("hhmm")[2:]
  return f"{date}T{hh}:{mm}:00Z"


def _parse_slug_from_filename(name: str) -> str | None:
  m = _FILENAME_RE.match(name)
  return m.group("slug") if m else None


def _parse_since(since: str) -> datetime | None:
  """Parse `since` param into a UTC datetime. Accepts trailing 'Z' or offset."""
  if not since:
    return None
  s = since.strip()
  # Normalize 'Z' to '+00:00' for fromisoformat (Python <3.11 quirk fixed
  # in 3.11 but explicit is clearer).
  if s.endswith("Z"):
    s = s[:-1] + "+00:00"
  try:
    dt = datetime.fromisoformat(s)
  except ValueError:
    return None
  # Coerce to UTC.
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc)


async def run_read(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
) -> dict[str, Any]:
  to = arguments.get("to")
  since_raw = arguments.get("since")
  unread_only = arguments.get("unread_only", True)
  limit_raw = arguments.get("limit", _DEFAULT_LIMIT)
  max_body_kb_raw = arguments.get("max_body_kb", _DEFAULT_TRUNCATE_KB)

  if not isinstance(to, str) or not to.strip():
    return _error_read("Missing required argument: 'to'.")
  to_norm = to.strip().lower()
  if to_norm not in _ALLOWED_TARGETS:
    return _error_read(
      f"'to' value {to!r} is not in the allowed target set. Allowed: "
      + ", ".join(sorted(_ALLOWED_TARGETS)) + ".",
      to=to,
    )
  try:
    limit = int(limit_raw)
    if limit <= 0:
      raise ValueError
    if limit > _MAX_LIMIT:
      limit = _MAX_LIMIT
  except (TypeError, ValueError):
    return _error_read(f"'limit' must be a positive integer, got {limit_raw!r}.")
  try:
    max_body_kb = float(max_body_kb_raw)
    if max_body_kb <= 0:
      raise ValueError
  except (TypeError, ValueError):
    return _error_read(
      f"'max_body_kb' must be a positive number, got {max_body_kb_raw!r}."
    )
  max_body_bytes = int(max_body_kb * 1024)

  since_dt = _parse_since(since_raw) if isinstance(since_raw, str) else None
  if since_raw and since_dt is None:
    return _error_read(
      f"'since' timestamp {since_raw!r} is not parseable. Expected ISO "
      "(YYYY-MM-DDTHH:MM:SSZ)."
    )

  root = _messages_root()
  pending_dir = root / "pending" / f"to-{to_norm}"
  read_dir = root / "read" / f"to-{to_norm}"
  # Nothing in EITHER branch? Return empty result cleanly — not an
  # error. Checking both matters: a recipient whose whole inbox has
  # been read has no pending/ dir but is not "no inbox".
  if not pending_dir.is_dir() and not read_dir.is_dir():
    return {
      "content": [{
        "type": "text",
        "text": f"No inbox at {pending_dir}. Zero messages.",
      }],
      "structuredContent": {
        "messages": [], "total_count": 0, "returned_count": 0,
      },
      "isError": False,
    }

  # Enumerate candidate files. Unread: pending/to-<to>/from-*/*.md.
  # Read (only if unread_only=False): read/to-<to>/from-*/*.md.
  # Both branches are now from-<sender>-scoped, so sender attribution
  # survives being marked read — it did not under the old flat done/.
  candidates: list[Path] = []
  for from_dir in sorted(pending_dir.glob("from-*")):
    if not from_dir.is_dir():
      continue
    for f in sorted(from_dir.glob("*.md")):
      if f.is_file():
        candidates.append(f)
  if not unread_only:
    for from_dir in sorted(read_dir.glob("from-*")):
      if not from_dir.is_dir():
        continue
      for f in sorted(from_dir.glob("*.md")):
        if f.is_file():
          candidates.append(f)

  # Filter by since.
  filtered: list[tuple[Path, str]] = []  # (path, timestamp_iso)
  for p in candidates:
    ts_iso = _parse_timestamp_from_filename(p.name)
    if ts_iso is None:
      # Fall back to mtime for non-conforming filenames.
      ts_iso = datetime.fromtimestamp(
        p.stat().st_mtime, tz=timezone.utc,
      ).strftime("%Y-%m-%dT%H:%M:%SZ")
    if since_dt is not None:
      # Parse ts_iso to compare.
      msg_dt = _parse_since(ts_iso)
      if msg_dt is None or msg_dt <= since_dt:
        continue
    filtered.append((p, ts_iso))

  # Sort newest-first by timestamp.
  filtered.sort(key=lambda pair: pair[1], reverse=True)
  total_count = len(filtered)

  # Take up to `limit` and build response.
  taken = filtered[:limit]
  messages: list[dict[str, Any]] = []
  for path, ts_iso in taken:
    try:
      body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
      body = f"[read error: {type(exc).__name__}: {exc}]"
    size_bytes = len(body.encode("utf-8"))
    truncated = False
    if size_bytes > max_body_bytes:
      # Truncate to max_body_bytes-ish, preserving marker.
      body = body.encode("utf-8")[:max_body_bytes].decode("utf-8", errors="ignore")
      body += f"\n\n[TRUNCATED: original was {size_bytes} bytes]"
      truncated = True
    # Extract `from` from parent directory (from-<X>). Post-restructure
    # this works in BOTH branches — read/ is from-scoped too, so being
    # marked read no longer erases the sender. The one exception is
    # `from-legacy/`, the synthetic bucket the one-time migration used
    # for files that were in the old flat `done/` (and the two that sat
    # at a recipient root); their sender was already unrecoverable
    # before the migration ran.
    # drain 2026-07-31-1110: `from-legacy/` reports as "(unknown)", not
    # "legacy". `legacy` names the migration's directory convention; the
    # semantic truth is that the sender identity was destroyed by the old
    # flat `done/` before this tree existed. Reusing the existing
    # parenthesized sentinel rather than a bare "unknown" keeps it
    # unambiguously not-a-cowork-name — a bare one would be
    # indistinguishable from a peer actually called `unknown`.
    parent = path.parent.name
    if parent == "from-legacy":
      from_norm = "(unknown)"
    elif parent.startswith("from-"):
      from_norm = parent[len("from-"):]
    else:
      from_norm = "(unknown)"
    slug = _parse_slug_from_filename(path.name) or ""
    messages.append({
      "path": str(path),
      "from": from_norm,
      "timestamp_utc": ts_iso,
      "slug": slug,
      "body": body,
      "size_bytes": size_bytes,
      "truncated": truncated,
    })

  return {
    "content": [{
      "type": "text",
      "text": (
        f"Read {len(messages)} of {total_count} matching message(s) "
        f"from to-{to_norm}/."
      ),
    }],
    "structuredContent": {
      "messages": messages,
      "total_count": total_count,
      "returned_count": len(messages),
    },
    "isError": False,
  }


async def run_mark(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001
) -> dict[str, Any]:
  path_raw = arguments.get("path")
  if not isinstance(path_raw, str) or not path_raw.strip():
    return _error_mark("Missing required argument: 'path'.")
  root = _messages_root()
  path = Path(path_raw).expanduser().resolve()
  # Path-scope check.
  try:
    path.relative_to(root)
  except ValueError:
    return _error_mark(
      f"path {path_raw!r} resolves outside messages root {root}. Refusing.",
      path=path_raw,
    )

  # Expect: <root>/pending/to-<X>/from-<Y>/<file>.md
  # Marking read swaps the leading branch segment and keeps everything
  # below it, so to-<X>/from-<Y>/ — and therefore the sender — survives.
  rel_parts = path.relative_to(root).parts
  if len(rel_parts) != 4:
    return _error_mark(
      f"path {path_raw!r} is not of the form "
      f"<messages>/pending/to-<cowork>/from-<cowork>/<file>.md. "
      f"Cannot mark read.",
      path=path_raw,
    )
  branch, to_part, from_part, _fname = rel_parts

  # Already in read/ → idempotent success.
  if branch == "read":
    return {
      "content": [{
        "type": "text",
        "text": f"Already marked read: {path}",
      }],
      "structuredContent": {"path": str(path), "marked_read": True},
      "isError": False,
    }
  if branch != "pending":
    return _error_mark(
      f"path {path_raw!r} is not under `pending/` or `read/`. "
      f"Cannot mark read.",
      path=path_raw,
    )
  if not to_part.startswith("to-") or not from_part.startswith("from-"):
    return _error_mark(
      f"path {path_raw!r} is not in a `to-<cowork>/from-<cowork>/` "
      f"subdirectory of pending/. Cannot mark read.",
      path=path_raw,
    )

  read_dir = root / "read" / to_part / from_part
  if not path.is_file():
    # Already moved by someone else — idempotent.
    read_target = read_dir / path.name
    if read_target.is_file():
      return {
        "content": [{
          "type": "text",
          "text": f"Already marked read at {read_target} (source no longer exists).",
        }],
        "structuredContent": {"path": str(read_target), "marked_read": True},
        "isError": False,
      }
    return _error_mark(
      f"path {path_raw!r} does not exist.",
      path=path_raw,
    )

  read_dir.mkdir(parents=True, exist_ok=True)
  target = read_dir / path.name
  # Collision-suffix on rare same-file-moved-twice-with-changes.
  suffix = 2
  final_target = target
  while final_target.exists():
    final_target = read_dir / f"{path.stem}-{suffix}{path.suffix}"
    suffix += 1
    if suffix > 100:
      return _error_mark(
        f"Refusing to create >100 collision-suffixed read/ entries for {path.name}.",
        path=path_raw,
      )
  path.rename(final_target)
  # Prune the now-empty pending dirs so the monitoring website's
  # `pending/**` watch doesn't show hollow directories for inboxes
  # that are fully read.
  for stale in (path.parent, path.parent.parent):
    try:
      stale.rmdir()
    except OSError:
      break
  return {
    "content": [{
      "type": "text",
      "text": f"Marked read: moved to {final_target}",
    }],
    "structuredContent": {"path": str(final_target), "marked_read": True},
    "isError": False,
  }
