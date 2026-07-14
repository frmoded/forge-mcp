"""`forge_get_run_result` — fetch the full result of a previous run.

CW-MCP-2-B. Wraps forge-transpile's `GET /run/{run_id}`.
"""
from __future__ import annotations

from typing import Any

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceHTTPError,
)
from ..schemas import GetRunResult

TOOL_NAME = "forge_get_run_result"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["run_id"],
  "properties": {
    "run_id": {
      "type": "string",
      "description": "The run_id returned by a previous forge_run_recipe call.",
    }
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  # Drain CW-MCP-3-A — tightened to match `GetRunResult` Pydantic model
  # (schemas.py). Every field is populated on success; declaring them as
  # required lets MCP clients rely on presence without null-guarding
  # each read.
  "required": [
    "run_id", "created_at", "expires_at", "duration_ms",
    "exit_code", "timed_out", "stdout", "stderr", "artifacts",
  ],
  "properties": {
    "run_id": {"type": "string"},
    "created_at": {"type": "integer"},
    "expires_at": {"type": "integer"},
    "duration_ms": {"type": "integer"},
    "exit_code": {"type": "integer"},
    "timed_out": {"type": "boolean"},
    "stdout": {"type": "string"},
    "stderr": {"type": "string"},
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
  },
}

DESCRIPTION = (
  "Fetch the full stdout/stderr + artifact manifest for a previous "
  "forge_run_recipe call. Returns isError=true if the run has expired "
  "(7-day TTL) or if the run_id was created under a different Bearer "
  "token."
)


async def run(
  arguments: dict[str, Any],
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns MCP tool-result shape."""
  run_id = arguments.get("run_id", "")
  if not isinstance(run_id, str) or run_id == "":
    return {
      "content": [
        {"type": "text", "text": "Missing required argument: 'run_id'."}
      ],
      "structuredContent": {},
      "isError": True,
    }

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      result: GetRunResult = await client.get_run_result(
        run_id=run_id, bearer=bearer,
      )
    except ForgeServiceHTTPError as exc:
      if exc.status_code == 404:
        return {
          "content": [
            {
              "type": "text",
              "text": (
                f"Run {run_id!r} not found. Either it expired (7-day TTL), "
                f"was created under a different Bearer token, or the id is "
                f"invalid."
              ),
            }
          ],
          "structuredContent": {},
          "isError": True,
        }
      if exc.status_code in (401, 403):
        return {
          "content": [
            {
              "type": "text",
              "text": (
                f"forge-transpile rejected the Bearer token (HTTP {exc.status_code}). "
                f"Rotate FORGE_MCP_BEARER and retry."
              ),
            }
          ],
          "structuredContent": {},
          "isError": True,
        }
      return {
        "content": [
          {
            "type": "text",
            "text": f"Get-run request failed with HTTP {exc.status_code}.",
          }
        ],
        "structuredContent": {},
        "isError": True,
      }
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  # Success. Summary text mirrors run_recipe's preview shape.
  bits = [
    f"exit={result.exit_code}",
    f"{result.duration_ms}ms",
    f"{len(result.artifacts)} artifact(s)",
  ]
  if result.timed_out:
    bits.append("TIMED OUT")
  header = f"Run {result.run_id[:8]}… — {', '.join(bits)}"
  body_bits: list[str] = [header]
  if result.stdout:
    body_bits.append(f"\nSTDOUT:\n{result.stdout[:2000]}")
  if result.stderr:
    body_bits.append(f"\nSTDERR:\n{result.stderr[:2000]}")
  return {
    "content": [{"type": "text", "text": "\n".join(body_bits)}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
