"""Shared structured error shape for forge-mcp tool responses.

CW-plugin-plus-mcp-structured-error-format-parity (drain
2026-08-08-1300). The 3-field shape — ``cause`` / ``suggested_fix`` /
``details`` — is shared verbatim with the plugin's Forge Output panel
(forge-client-obsidian ``src/forge-error-core.ts``) so failures render
identically across surfaces. Spec:
``~/projects/forge/docs/specs/error-format.md``.

Rendering conventions:

- ``content`` carries TWO human-readable text items — cause first,
  suggested fix second — so text-only MCP clients still see both.
  ``details`` deliberately stays OUT of the content array (it is the
  traceback/debug dump; the plugin renders it collapsed, and MCP
  clients that want it read ``structuredContent``).
- ``structuredContent`` merges the three fields ALONGSIDE the tool's
  own OUTPUT_SCHEMA-required fields (passed via ``structured_base``).
  This is a deliberate deviation from replacing the payload wholesale:
  every tool's outputSchema pins required keys, and clients already
  parse them on error responses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ForgeError:
  """One-sentence ``cause``, one-sentence actionable ``suggested_fix``,
  optional ``details`` dump. Field names match the plugin's interface —
  parity is the point."""

  cause: str
  suggested_fix: str
  details: str | None = None


def to_tool_response(
  err: ForgeError,
  *,
  structured_base: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Build the standard ``isError: True`` tool response for *err*.

  ``structured_base`` supplies the tool's OUTPUT_SCHEMA-required
  structuredContent fields (empty-shape placeholders by convention);
  the 3-field error shape is merged on top. ``details`` is omitted
  from structuredContent when absent (absent beats empty)."""
  structured: dict[str, Any] = dict(structured_base or {})
  structured["cause"] = err.cause
  structured["suggested_fix"] = err.suggested_fix
  if err.details is not None and err.details.strip():
    structured["details"] = err.details
  return {
    "content": [
      {"type": "text", "text": err.cause},
      {"type": "text", "text": err.suggested_fix},
    ],
    "structuredContent": structured,
    "isError": True,
  }
