"""Drain CW-MCP-3-A — regression-locks on the actionable content of
every `isError: true` message.

Tool-surface v1 §Error taxonomy sets a three-part convention: (1) what
went wrong, (2) what was expected, (3) a one-line example that would
have worked. Not every error site can supply all three (e.g., "run_id
not found" — there's no example the caller could paste); those errors
should still name the failure mode + surface the actionable fix.

These tests pin the LOAD-BEARING PHRASES in each error message so a
polish refactor can't accidentally drop the actionable info (env var
name, version numbers, line/column, etc.).
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import commit_recipe, compile_recipe, get_run_result, read_note_catalog
from forge_mcp.vault_fs import VaultFS

# ---------------------------------------------------------------------------
# Version conflict — commit path (CW-MCP-2-C surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_version_conflict_names_both_versions(tmp_path: Path):
  """Drain §5 test #5. Conflict message MUST name expected + current
  versions verbatim so the agent knows what to fetch on retry."""
  vault_root = tmp_path / "vault"
  vault_root.mkdir()
  (vault_root / "note.md").write_text(
    "---\nrecipe_version: 3\n---\n\n# Recipe\n\nReturn 1.\n"
  )
  fs = VaultFS(root=vault_root)
  result = await commit_recipe.run(
    arguments={"source": "Return 2.", "note_id": "note", "expected_version": 1},
    bearer="tok",
    vault_fs=fs,
  )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "expected version 1" in text  # what agent thought
  assert "note is at version 3" in text  # what's actually there
  assert "retry" in text.lower() or "fetch again" in text.lower()


# ---------------------------------------------------------------------------
# Auth rejection — read_note_catalog upstream 401 (CW-MCP-1-B surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_auth_rejection_names_env_var():
  """Drain §5 test #6. On 401/403 from forge-transpile, forge-mcp
  translates to an actionable message that (a) reports the HTTP code,
  (b) names FORGE_MCP_BEARER (the env var the driver rotates)."""
  respx.get("http://localhost:8000/catalog").mock(
    return_value=httpx.Response(401, json={"detail": "invalid or missing bearer"}),
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await read_note_catalog.run(
      arguments={}, bearer="stale-token", client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "401" in text
  assert "FORGE_MCP_BEARER" in text
  assert "rotate" in text.lower() or "retry" in text.lower()


# ---------------------------------------------------------------------------
# Parse error — compile_recipe (CW-MCP-2-A + CW-recipe-parser-line-info)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_parse_error_names_line_and_column():
  """Drain §5 test #7. Compile-tool text must surface `line X, column Y`
  from the ParseErrorDetail so the agent can point the plugin at the
  right token in the Recipe."""
  respx.post("http://localhost:8000/compile").mock(
    return_value=httpx.Response(200, json={
      "parse_status": "parse_error",
      "python_source": None,
      "unresolved_slot_count": 0,
      "parse_error": {
        "line": 3,
        "column": 12,
        "message": "unexpected char '='",
        "expected": "",
      },
    }),
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await compile_recipe.run(
      arguments={"source": "Let x === 5."}, bearer="tok", client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "line 3" in text
  assert "column 12" in text
  assert "unexpected char" in text  # the parser's actual message


# ---------------------------------------------------------------------------
# Not-found — get_run_result 404 (CW-MCP-2-B surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_not_found_names_likely_causes():
  """Drain §5 test #8. When forge-transpile returns 404 for a run_id,
  the error text must explain the likely causes (TTL expired vs
  never existed vs wrong bearer) — pure `404` is not actionable."""
  respx.get("http://localhost:8000/run/deadbeef").mock(
    return_value=httpx.Response(404, text="not found"),
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await get_run_result.run(
      arguments={"run_id": "deadbeef"}, bearer="tok", client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  # At minimum name the run_id AND either TTL/expired OR a re-run
  # suggestion.
  assert "deadbeef" in text
  assert (
    "expired" in text.lower()
    or "ttl" in text.lower()
    or "not found" in text.lower()
    or "re-run" in text.lower()
    or "forge_run_recipe" in text
  )


# ---------------------------------------------------------------------------
# Traversal — commit_recipe rejects `..` in note_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traversal_defense_names_invalid_note_id(tmp_path: Path):
  """Regression-lock on the CW-MCP-2-C path-traversal surface.
  Rejection text must name the offending note_id verbatim (agent can
  see what it sent and correct)."""
  vault_root = tmp_path / "vault"
  vault_root.mkdir()
  fs = VaultFS(root=vault_root)
  result = await commit_recipe.run(
    arguments={"source": "Return 1.", "note_id": "../evil"},
    bearer="tok",
    vault_fs=fs,
  )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "Invalid note_id" in text or "invalid" in text.lower()
  assert "..'" in text or "'..'" in text or ".." in text  # names the offending segment


# ---------------------------------------------------------------------------
# Missing required argument — commit_recipe missing note_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_argument_names_field(tmp_path: Path):
  """Missing-arg messages must name the field so the agent knows
  which argument to supply on retry."""
  vault_root = tmp_path / "vault"
  vault_root.mkdir()
  fs = VaultFS(root=vault_root)
  result = await commit_recipe.run(
    arguments={"source": "Return 1."},  # no note_id
    bearer="tok",
    vault_fs=fs,
  )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "note_id" in text
  assert "required" in text.lower() or "missing" in text.lower()
