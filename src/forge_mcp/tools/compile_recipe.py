"""`forge_compile_recipe` — deterministically transpile an E-- Recipe to Python.

CW-MCP-2-A. Pure, deterministic, no LLM, no execution, no vault write.
The agent hands us E-- source text; we return either the compiled Python
(ready to feed to `forge_run_recipe`) or a structured parse error that
names line/column/message so the agent can fix and retry.

Wire spec: `forge-mcp-tool-surface-v1.md` §Tool surface → `forge_compile_recipe`.
"""
from __future__ import annotations

from typing import Any

from ..forge_service_client import (
  ForgeServiceClient,
  ForgeServiceEndpointMissing,
  ForgeServiceHTTPError,
)
from ..schemas import CompileResult

TOOL_NAME = "forge_compile_recipe"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["source"],
  "properties": {
    "source": {
      "type": "string",
      "description": "E-- Recipe source text to compile deterministically to Python.",
    }
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["parse_status"],
  "properties": {
    "parse_status": {"type": "string", "enum": ["ok", "parse_error"]},
    "python_source": {"type": ["string", "null"]},
    "unresolved_slot_count": {"type": "integer", "minimum": 0},
    "parse_error": {
      "type": ["object", "null"],
      "properties": {
        "line": {"type": "integer", "minimum": 0},
        "column": {"type": "integer", "minimum": 0},
        "message": {"type": "string"},
        "expected": {"type": "string"},
      },
    },
  },
}

DESCRIPTION = (
  "Deterministically compile an E-- Recipe to Python. No LLM, no execution, "
  "no vault write. Use to parse-check syntax before calling forge_run_recipe. "
  "Parse errors return isError=true with structured line/column/message; "
  "successful compiles include the Python source + a slot count so agents "
  "know whether to call /resolve-slot before running."
)


def _parse_error_summary(parse_error: dict[str, Any]) -> str:
  """Three-part user-facing text: what went wrong / expected / example.

  The parse-error dict from forge-transpile carries `line`, `column`,
  `message`, and (usually empty) `expected`. Line=0 or column=0 means
  the parser didn't surface a position; omit them from the header in
  that case rather than emit misleading "line 0" text.
  """
  line = parse_error.get("line", 0) or 0
  column = parse_error.get("column", 0) or 0
  message = parse_error.get("message", "unknown parse error")
  expected = parse_error.get("expected", "") or ""

  if line and column:
    header = f"Parse error at line {line}, column {column}: {message}"
  elif line:
    header = f"Parse error at line {line}: {message}"
  else:
    header = f"Parse error: {message}"

  if expected:
    return f"{header}\nExpected: {expected}"
  return header


async def run(
  arguments: dict[str, Any],
  bearer: str,
  client: ForgeServiceClient | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns the MCP tool-result shape.

  On success: `isError: false`, `structuredContent = CompileResult.model_dump(...)`,
  text summarizes "compiled OK; N unresolved slots".

  On parse error: `isError: true`, `structuredContent = CompileResult`
  with `parse_status="parse_error"`, text is the three-part diagnostic.

  On auth failure or upstream 5xx: `isError: true` with an actionable
  message (mirror CW-MCP-1-B's translation pattern).
  """
  source = arguments.get("source", "")
  if not isinstance(source, str) or source == "":
    return {
      "content": [
        {"type": "text", "text": "Missing required argument: 'source' (E-- Recipe text)."}
      ],
      "structuredContent": {"parse_status": "parse_error"},
      "isError": True,
    }

  owns_client = client is None
  if client is None:
    client = ForgeServiceClient()
    await client.__aenter__()

  try:
    try:
      result: CompileResult = await client.compile_recipe(source=source, bearer=bearer)
    except ForgeServiceEndpointMissing as exc:
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Compile is currently unavailable: {exc}. "
              f"This means forge-transpile predates drain 2740 — deploy the "
              f"latest before retrying."
            ),
          }
        ],
        "structuredContent": {"parse_status": "parse_error"},
        "isError": True,
      }
    except ForgeServiceHTTPError as exc:
      if exc.status_code in (401, 403):
        # CW-MCP-1-B — auth failures name the failure mode + point at
        # the env var to rotate.
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
          "structuredContent": {"parse_status": "parse_error"},
          "isError": True,
        }
      return {
        "content": [
          {
            "type": "text",
            "text": (
              f"Compile request failed with HTTP {exc.status_code}. "
              f"Retry or check forge-transpile logs."
            ),
          }
        ],
        "structuredContent": {"parse_status": "parse_error"},
        "isError": True,
      }
  finally:
    if owns_client:
      await client.__aexit__(None, None, None)

  # Business error: parse failure. HTTP was 200, but the Recipe didn't parse.
  if result.parse_status == "parse_error":
    parse_error_dict = result.parse_error.model_dump() if result.parse_error else {}
    text = _parse_error_summary(parse_error_dict)
    return {
      "content": [{"type": "text", "text": text}],
      "structuredContent": result.model_dump(mode="json"),
      "isError": True,
    }

  # Success.
  slot_hint = (
    f" ({result.unresolved_slot_count} unresolved slot(s) — call /resolve-slot "
    f"before /run)"
    if result.unresolved_slot_count > 0
    else ""
  )
  return {
    "content": [
      {
        "type": "text",
        "text": f"Compiled OK{slot_hint}.",
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
