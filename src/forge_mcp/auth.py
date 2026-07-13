"""Per-request Bearer extraction.

CW-MCP-1-B replaces CW-MCP-1-A's env-var / ContextVar stub with actual
per-request `Authorization` header extraction from the Streamable HTTP
transport. forge-mcp does NOT validate the token — forge-transpile is
the source of truth for validity (`require_bearer_token` guarded by
`FORGE_TRANSPILE_SECRET`). This module just:

  1. Peels `Bearer <token>` out of the header, or reports why it can't.
  2. Reads that header off the active Starlette request via FastMCP's
     `Context.request_context.request`.

Auth failures (missing or malformed header) surface as
`AUTH_MISSING` / `AUTH_MALFORMED` error codes so tool handlers can
translate to MCP `isError: true` with an actionable message the agent
sees directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class BearerExtractionError(Exception):
  """Base for bearer-extraction failures.

  `code` is a stable string identifier for the failure mode. Tool
  handlers map codes → user-facing messages.
  """

  code: str = "AUTH_UNKNOWN"


class BearerMissingError(BearerExtractionError):
  code = "AUTH_MISSING"


class BearerMalformedError(BearerExtractionError):
  code = "AUTH_MALFORMED"


@dataclass(frozen=True)
class ExtractedBearer:
  """Successfully extracted Bearer token."""

  token: str


def extract_bearer_from_header(header_value: str | None) -> ExtractedBearer:
  """Parse a raw `Authorization` header value.

  Returns the extracted token on success. Raises `BearerMissingError`
  when the header is absent/empty; raises `BearerMalformedError` when
  the header exists but isn't a well-formed `Bearer <token>` pair.

  The check is deliberately strict: the scheme must be exactly
  "bearer" (case-insensitive), separated from the token by a single
  ASCII space, and the token must be non-empty. Anything else is
  malformed — no lenient fallback so the caller doesn't accidentally
  forward garbage to forge-transpile.
  """
  if header_value is None or header_value.strip() == "":
    raise BearerMissingError(
      "Authorization header missing. Configure your MCP client with a "
      "'Bearer <token>' — see docs/install.md."
    )
  parts = header_value.split(" ", 1)
  if len(parts) != 2:
    raise BearerMalformedError(
      "Authorization header malformed: expected 'Bearer <token>', got "
      f"a single word ({header_value!r})."
    )
  scheme, token = parts[0], parts[1].strip()
  if scheme.lower() != "bearer":
    raise BearerMalformedError(
      "Authorization header malformed: expected 'Bearer' scheme, got "
      f"{scheme!r}."
    )
  if not token:
    raise BearerMalformedError(
      "Authorization header malformed: 'Bearer ' with no token."
    )
  return ExtractedBearer(token=token)


def extract_bearer_from_request(request: Any) -> ExtractedBearer:
  """Pull the Authorization header off a Starlette-like request.

  Duck-typed on `request.headers.get(name)` so tests can pass any
  headers-shaped stub. In production this is the Starlette `Request`
  attached to `Context.request_context.request` by FastMCP's
  Streamable HTTP transport.
  """
  headers = getattr(request, "headers", None)
  if headers is None:
    raise BearerMissingError(
      "Request has no headers attribute — Streamable HTTP transport "
      "not attached to this call."
    )
  raw = headers.get("authorization") if hasattr(headers, "get") else None
  return extract_bearer_from_header(raw)


def auth_error_to_tool_result(exc: BearerExtractionError) -> dict[str, Any]:
  """Uniform MCP tool-result shape for a bearer-extraction failure.

  Tool handlers use this so a missing/malformed Authorization header
  surfaces as `isError: true` with a stable, actionable message the
  agent can read.
  """
  return {
    "content": [
      {
        "type": "text",
        "text": str(exc),
      }
    ],
    "structuredContent": {"notes": []},
    "isError": True,
  }
