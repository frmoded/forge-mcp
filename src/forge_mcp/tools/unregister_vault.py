"""`forge_unregister_vault` — runtime vault removal.

CW-MCP-runtime-vault-registration. Removes a vault from the live
VaultRegistry. Safety invariant: refuses to remove the last remaining
vault (subsequent tool calls need something to target).

Filesystem side effects: NONE. Vault directory + notes stay put.
"""
from __future__ import annotations

from typing import Any

from ..schemas import UnregisterVaultResult
from ..vault_registry import (
  LastVaultRemovalError,
  VaultNotFoundError,
  VaultRegistry,
)

TOOL_NAME = "forge_unregister_vault"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["name"],
  "properties": {
    "name": {
      "type": "string",
      "description": "Vault name to remove (from forge_list_vaults).",
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["unregistered", "remaining_vaults"],
  "properties": {
    "unregistered": {"type": "boolean"},
    "remaining_vaults": {
      "type": "array",
      "items": {"type": "string"},
    },
  },
}

DESCRIPTION = (
  "Remove a vault from the live forge-mcp registry. Filesystem is "
  "untouched — only the registry mapping changes. Refuses if `name` "
  "is the last remaining vault (agents always need at least one "
  "target). Removals do NOT persist across server restart — set "
  "FORGE_VAULTS for durable configuration."
)


def _error(text: str, *, remaining: list[str]) -> dict[str, Any]:
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": {
      "unregistered": False,
      "remaining_vaults": remaining,
    },
    "isError": True,
  }


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — no upstream call
  vault_registry: VaultRegistry,
) -> dict[str, Any]:
  name = arguments.get("name")

  if not isinstance(name, str) or not name.strip():
    return _error(
      "Missing required argument: 'name' (vault to unregister).",
      remaining=vault_registry.names(),
    )

  try:
    vault_registry.remove(name)
  except VaultNotFoundError as exc:
    return _error(str(exc), remaining=vault_registry.names())
  except LastVaultRemovalError as exc:
    return _error(str(exc), remaining=vault_registry.names())

  remaining = vault_registry.names()
  result = UnregisterVaultResult(unregistered=True, remaining_vaults=remaining)
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Unregistered vault {name!r}. Remaining vaults: "
          f"{', '.join(remaining) or '(none)'}. Filesystem untouched."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
