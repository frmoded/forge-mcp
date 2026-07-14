"""`forge_run_recipe` — compile + execute an E-- Recipe in the server sandbox.

CW-MCP-2-B. Wraps forge-transpile's `POST /run`. Reuses CW-MCP-1-B's
per-request Bearer extraction + auth-failure translation.

Returns a short preview + `run_id` for follow-up via `forge_get_run_result`.
"""
from __future__ import annotations

from typing import Any

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
)
from ..schemas import RunResult

TOOL_NAME = "forge_run_recipe"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["source"],
  "properties": {
    "source": {
      "type": "string",
      "description": "E-- Recipe source text. Compiled + executed server-side in a resource-limited sandbox.",
    },
    "domains": {
      "type": "array",
      "items": {"type": "string"},
      "default": ["music"],
      "description": (
        "Library-note domains to bind into the sandbox namespace "
        "(e.g. ['music'], ['moda']). Defaults to ['music'] when omitted."
      ),
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  # Drain CW-MCP-3-A — every field except `parse_error` is populated
  # even on parse-error runs (run_id/duration/exit are 0/"" but present).
  "required": [
    "parse_status", "run_id", "duration_ms", "exit_code",
    "timed_out", "stdout_preview", "artifacts",
  ],
  "properties": {
    "parse_status": {"type": "string", "enum": ["ok", "parse_error"]},
    "run_id": {"type": "string"},
    "duration_ms": {"type": "integer", "minimum": 0},
    "exit_code": {"type": "integer"},
    "timed_out": {"type": "boolean"},
    "stdout_preview": {"type": "string"},
    "artifacts": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "mime_type", "size_bytes", "uri"],
        "properties": {
          "name": {"type": "string"},
          "mime_type": {"type": "string"},
          "size_bytes": {"type": "integer", "minimum": 0},
          "uri": {"type": "string"},
        },
      },
    },
    "parse_error": {"type": ["object", "null"]},
  },
}

DESCRIPTION = (
  "Compile + run an E-- Recipe in a resource-limited server sandbox. "
  "Returns a short preview + a run_id; call forge_get_run_result to fetch "
  "the full stdout/stderr + artifact manifest. Artifacts (MusicXML, MIDI, "
  "PNGs) are accessible as forge-artifact:///{run_id}/{name} resources."
)


def _preview_text(result: RunResult) -> str:
  """Compact status line for the tool's `content[0].text`."""
  if result.parse_status == "parse_error":
    pe = result.parse_error
    if pe is None:
      return "Parse error (no details available)."
    if pe.line and pe.column:
      loc = f"line {pe.line}, column {pe.column}"
    elif pe.line:
      loc = f"line {pe.line}"
    else:
      loc = "unknown location"
    return f"Parse error at {loc}: {pe.message}"

  bits = [f"exit={result.exit_code}", f"{result.duration_ms}ms"]
  if result.timed_out:
    bits.append("TIMED OUT")
  if result.artifacts:
    bits.append(f"{len(result.artifacts)} artifact(s)")
  header = f"Run {result.run_id[:8]}… — {', '.join(bits)}"
  if result.stdout_preview:
    return f"{header}\n\n{result.stdout_preview[:1200]}"
  return header


async def run(
  arguments: dict[str, Any],
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns MCP tool-result shape.

  A parse error, timeout, or non-zero exit surfaces as `isError:true`
  with a text preview + the full RunResult in `structuredContent` so
  the agent has both an at-a-glance signal AND the structured payload
  for programmatic follow-up.

  Success (parse ok + exit 0) returns `isError:false`.
  """
  source = arguments.get("source", "")
  if not isinstance(source, str) or source == "":
    return {
      "content": [
        {"type": "text", "text": "Missing required argument: 'source' (E-- Recipe text)."}
      ],
      "structuredContent": {"parse_status": "parse_error", "run_id": ""},
      "isError": True,
    }
  # Drain 2900 — domains selects which library-note callables get bound.
  # Default to ["music"] to match forge-transpile's server-side default,
  # but pass it explicitly so the outbound HTTP body names the domain.
  raw_domains = arguments.get("domains", ["music"])
  if isinstance(raw_domains, list) and all(isinstance(d, str) for d in raw_domains):
    domains = raw_domains
  else:
    domains = ["music"]

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      result: RunResult = await client.run_recipe(
        source=source, bearer=bearer, domains=domains,
      )
    except ForgeServiceEndpointMissing as exc:
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Run is currently unavailable: {exc}. "
              f"forge-transpile predates drain 2790 — deploy the latest."
            ),
          }
        ],
        "structuredContent": {"parse_status": "parse_error", "run_id": ""},
        "isError": True,
      }
    except ForgeServiceHTTPError as exc:
      if exc.status_code in (401, 403):
        return {
          "content": [
            {
              "type": "text",
              "text": (
                f"forge-transpile rejected the Bearer token (HTTP {exc.status_code} "
                f"invalid token). Rotate FORGE_MCP_BEARER in your MCP client "
                f"config and retry."
              ),
            }
          ],
          "structuredContent": {"parse_status": "parse_error", "run_id": ""},
          "isError": True,
        }
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Run request failed with HTTP {exc.status_code}. "
              f"Retry or check forge-transpile logs."
            ),
          }
        ],
        "structuredContent": {"parse_status": "parse_error", "run_id": ""},
        "isError": True,
      }
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  # Business signal: parse error OR non-zero exit OR timeout → isError=true.
  is_error = (
    result.parse_status == "parse_error"
    or result.exit_code != 0
    or result.timed_out
  )
  return {
    "content": [{"type": "text", "text": _preview_text(result)}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": is_error,
  }
