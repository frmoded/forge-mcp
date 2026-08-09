"""Tests for the shared structured error shape (drain 2026-08-08-1300).

Parity contract with the plugin's forge-error-core.ts: 3 fields
(cause / suggested_fix / details), cause + fix as the two content text
items, details only in structuredContent, tool OUTPUT_SCHEMA-required
fields preserved via structured_base.
"""
from __future__ import annotations

import pytest

from forge_mcp.error_response import ForgeError, to_tool_response
from forge_mcp.tools import read_note
from forge_mcp.vault_registry import VaultRegistry


def test_to_tool_response_shape() -> None:
  err = ForgeError(
    cause="Two notes share basename X.",
    suggested_fix="Rename one to disambiguate.",
    details="Traceback ...",
  )
  res = to_tool_response(err)
  assert res["isError"] is True
  assert res["content"] == [
    {"type": "text", "text": "Two notes share basename X."},
    {"type": "text", "text": "Rename one to disambiguate."},
  ]
  sc = res["structuredContent"]
  assert sc["cause"] == "Two notes share basename X."
  assert sc["suggested_fix"] == "Rename one to disambiguate."
  assert sc["details"] == "Traceback ..."


def test_to_tool_response_omits_absent_details() -> None:
  res = to_tool_response(ForgeError(cause="c", suggested_fix="f"))
  assert "details" not in res["structuredContent"]
  # Whitespace-only details treated as absent (absent beats empty).
  res2 = to_tool_response(
    ForgeError(cause="c", suggested_fix="f", details="  \n ")
  )
  assert "details" not in res2["structuredContent"]


def test_to_tool_response_merges_structured_base() -> None:
  base = {"note": {"note_id": "x", "vault": "v"}}
  res = to_tool_response(
    ForgeError(cause="c", suggested_fix="f"), structured_base=base
  )
  sc = res["structuredContent"]
  # Tool OUTPUT_SCHEMA-required fields survive alongside the 3-shape.
  assert sc["note"] == {"note_id": "x", "vault": "v"}
  assert sc["cause"] == "c"
  assert sc["suggested_fix"] == "f"
  # The caller's base dict is not mutated.
  assert "cause" not in base


@pytest.mark.asyncio
async def test_read_note_error_carries_structured_3_shape(
  monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
  """Migrated site: unregistered vault → cause + fix in content order,
  3-shape in structuredContent, schema-required note placeholder kept."""
  vault = tmp_path / "v"
  vault.mkdir()
  monkeypatch.setenv("FORGE_VAULTS", f"v:{vault}")
  registry = VaultRegistry.from_env()
  res = await read_note.run(
    {"note_id": "foo", "vault": "nope"}, "", registry,
  )
  assert res["isError"] is True
  assert len(res["content"]) == 2
  assert "nope" in res["content"][0]["text"]
  assert "forge_list_vaults" in res["content"][1]["text"]
  sc = res["structuredContent"]
  assert sc["cause"] == res["content"][0]["text"]
  assert sc["suggested_fix"] == res["content"][1]["text"]
  assert sc["note"]["note_id"] == "foo"  # OUTPUT_SCHEMA placeholder kept
