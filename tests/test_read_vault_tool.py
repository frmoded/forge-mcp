"""End-to-end tests for the forge_read_notes_in_vault tool implementation."""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import read_notes_in_vault

VAULT_TWO_NOTES = {
  "notes": [
    {
      "note_id": "vault-1",
      "name": "hello_forge",
      "path": "vault/hello_forge.md",
      "state": "committed",
      # Drain 2026-07-12-1335 aligned this to Forge constitution §S9
      # vocabulary: lowercase {description, recipe, python, synced}.
      "source_facet": "recipe",
      "latest_recipe_version": 3,
    },
    {
      "note_id": "vault-2",
      "name": "greet",
      "path": "vault/greet.md",
      "state": "draft",
      "source_facet": "description",
      "latest_recipe_version": 0,
    },
  ]
}


@pytest.mark.asyncio
async def test_vault_tool_returns_structured_content_on_success() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/vault/notes").mock(
      return_value=httpx.Response(200, json=VAULT_TWO_NOTES)
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      result = await read_notes_in_vault.run(
        arguments={},
        bearer="tok",
        client=client,
      )

  assert result["isError"] is False
  assert len(result["structuredContent"]["notes"]) == 2
  assert result["structuredContent"]["notes"][0]["state"] == "committed"


@pytest.mark.asyncio
async def test_vault_tool_translates_missing_endpoint_to_business_error() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    mock.get("/vault/notes").mock(return_value=httpx.Response(404))
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      result = await read_notes_in_vault.run(
        arguments={},
        bearer="tok",
        client=client,
      )

  assert result["isError"] is True
  assert "/vault/notes" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_vault_tool_passes_filter_arg_through_to_query_string() -> None:
  async with respx.mock(base_url="http://localhost:8000") as mock:
    route = mock.get("/vault/notes", params={"filter": "hello"}).mock(
      return_value=httpx.Response(200, json={"notes": []})
    )
    async with ForgeServiceClient(base_url="http://localhost:8000") as client:
      await read_notes_in_vault.run(
        arguments={"filter": "hello"},
        bearer="tok",
        client=client,
      )
    assert route.called


@pytest.mark.asyncio
async def test_vault_tool_source_facet_enum_matches_s9_vocab() -> None:
  """Drain 2026-07-12-1335 regression: source_facet must accept the
  four S9-vocabulary tokens (description, recipe, python, synced) —
  not the pre-drain guess {Recipe, Description, None}."""
  from forge_mcp.schemas import VaultNoteEntry

  for facet in ("description", "recipe", "python", "synced"):
    entry = VaultNoteEntry(
      note_id="e", name="e", path="e.md",
      state="committed", source_facet=facet,  # type: ignore[arg-type]
      latest_recipe_version=0,
    )
    assert entry.source_facet == facet

  # Pre-drain values must now fail validation.
  for bad in ("Recipe", "Description", "None"):
    with pytest.raises(Exception):
      VaultNoteEntry(
        note_id="e", name="e", path="e.md",
        state="committed", source_facet=bad,  # type: ignore[arg-type]
        latest_recipe_version=0,
      )
