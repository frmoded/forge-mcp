"""Tests for the `vault` param on forge_run_recipe.

CW-forge-run-recipe-vault-note-invocation-arch-b-pivot
(drain 2026-07-27-1400).

Confirms tools/run_recipe.py resolves the vault-note closure + packages
it into the outbound HTTP body as `vault_notes`. Uses respx to capture
the request payload without actually hitting the transpile service.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import run_recipe
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


def _mk_ok_response(run_id: str = "runid00000000000000000000000000") -> dict:
  return {
    "parse_status": "ok",
    "run_id": run_id,
    "parse_error": None,
    "duration_ms": 10,
    "exit_code": 0,
    "timed_out": False,
    "stdout_preview": "",
    "artifacts": [],
  }


@pytest.fixture
def vault_registry(tmp_path: Path) -> VaultRegistry:
  root = tmp_path / "music"
  root.mkdir()
  vault = VaultFS(root=root)
  # Seed one action note the closure walker will find.
  (root / "hello_world.md").write_text(
    "---\n"
    "type: action\n"
    "recipe_version: 1\n"
    "---\n\n"
    "# Description\n\nsays hi.\n\n"
    "# Recipe\n\n"
    'Return "hello, world".\n'
  )
  return VaultRegistry({"music": vault})


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_with_vault_ships_closure_payload(
  vault_registry: VaultRegistry,
) -> None:
  """When `vault` is set, the outbound /run body carries a `vault_notes`
  entry for each referenced action note in the vault."""
  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok_response())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await run_recipe.run(
      arguments={
        "source": "Return Call [[hello_world]].\n",
        "vault": "music",
      },
      bearer="tok",
      client=client,
      vault_registry=vault_registry,
    )
  assert result["isError"] is False
  assert route.called
  body = route.calls[0].request.content
  import json as _json
  parsed = _json.loads(body)
  assert "vault_notes" in parsed
  assert len(parsed["vault_notes"]) == 1
  entry = parsed["vault_notes"][0]
  assert entry["name"] == "hello_world"
  assert 'Return "hello, world"' in entry["recipe_source"]


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_without_vault_omits_vault_notes(
  vault_registry: VaultRegistry,
) -> None:
  """When `vault` is not set, the outbound /run body has NO
  `vault_notes` field (preserves back-compat with older transpile)."""
  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok_response())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await run_recipe.run(
      arguments={"source": "Return 42.\n"},
      bearer="tok",
      client=client,
      vault_registry=vault_registry,
    )
  assert result["isError"] is False
  assert route.called
  import json as _json
  parsed = _json.loads(route.calls[0].request.content)
  assert "vault_notes" not in parsed


@pytest.mark.asyncio
async def test_run_recipe_vault_not_found_returns_isError(
  vault_registry: VaultRegistry,
) -> None:
  """Unknown vault name surfaces isError=True cleanly, no HTTP call."""
  result = await run_recipe.run(
    arguments={"source": "Return 1.\n", "vault": "nonexistent"},
    bearer="tok",
    vault_registry=vault_registry,
  )
  assert result["isError"] is True
  assert "nonexistent" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_run_recipe_vault_cycle_surfaces_error(tmp_path: Path) -> None:
  """A vault with a cycle surfaces isError with the cycle path."""
  root = tmp_path / "cyclic"
  root.mkdir()
  fs = VaultFS(root=root)
  (root / "a.md").write_text(
    "---\ntype: action\nrecipe_version: 1\n---\n\n"
    "# Recipe\n\nReturn Call [[b]].\n"
  )
  (root / "b.md").write_text(
    "---\ntype: action\nrecipe_version: 1\n---\n\n"
    "# Recipe\n\nReturn Call [[a]].\n"
  )
  reg = VaultRegistry({"cyclic": fs})
  result = await run_recipe.run(
    arguments={"source": "Return Call [[a]].\n", "vault": "cyclic"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is True
  msg = result["content"][0]["text"].lower()
  assert "cycle" in msg


@pytest.mark.asyncio
@respx.mock
async def test_run_recipe_transitive_vault_notes_all_packaged(
  tmp_path: Path,
) -> None:
  """Vault-note A references B — both surface in `vault_notes` payload."""
  root = tmp_path / "transitive"
  root.mkdir()
  (root / "outer.md").write_text(
    "---\ntype: action\nrecipe_version: 1\n---\n\n"
    "# Recipe\n\nReturn Call [[inner]].\n"
  )
  (root / "inner.md").write_text(
    "---\ntype: action\nrecipe_version: 1\n---\n\n"
    "# Recipe\n\nReturn 99.\n"
  )
  reg = VaultRegistry({"transitive": VaultFS(root=root)})
  route = respx.post("http://localhost:8000/run").mock(
    return_value=httpx.Response(200, json=_mk_ok_response())
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    await run_recipe.run(
      arguments={
        "source": "Return Call [[outer]].\n",
        "vault": "transitive",
      },
      bearer="tok",
      client=client,
      vault_registry=reg,
    )
  import json as _json
  parsed = _json.loads(route.calls[0].request.content)
  names = {entry["name"] for entry in parsed["vault_notes"]}
  assert names == {"outer", "inner"}
