"""Tool inputSchema/outputSchema roundtrip against the Pydantic models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_mcp.schemas import NoteEntry, VaultNoteEntry
from forge_mcp.tools import read_note_catalog, read_notes_in_vault


def test_catalog_output_schema_lists_every_note_field_as_required() -> None:
  item_schema = read_note_catalog.OUTPUT_SCHEMA["properties"]["notes"]["items"]
  assert set(item_schema["required"]) == {
    "name",
    "domain",
    "signature",
    "short_desc",
    "long_desc",
    "uri",
  }


def test_vault_output_schema_lists_every_note_field_as_required() -> None:
  """Drain CW-MCP-2-E — reshaped from the Sprint 1 speculative fields
  (state/source_facet/latest_recipe_version) to the actual-populated
  fields the local VaultFS walker returns. `recipe_version` stays
  optional at the JSON-schema level (nullable) since notes never
  committed via forge_commit_recipe don't have the stamp."""
  item_schema = read_notes_in_vault.OUTPUT_SCHEMA["properties"]["notes"]["items"]
  assert set(item_schema["required"]) == {
    "note_id",
    "name",
    "path",
    "has_recipe",
  }
  # recipe_version is present as a nullable property but not required.
  assert "recipe_version" in item_schema["properties"]
  assert item_schema["properties"]["recipe_version"]["type"] == ["integer", "null"]


def test_note_entry_roundtrips_through_pydantic() -> None:
  payload = {
    "name": "compose_blues",
    "domain": "music",
    "signature": "Call [[compose_blues]] with tempo=int returning MusicXML",
    "short_desc": "Compose a 12-bar blues progression.",
    "long_desc": "Longer description with usage guidance.",
    "uri": "forge-note:///music/compose_blues",
  }
  entry = NoteEntry.model_validate(payload)
  # roundtrip
  assert entry.model_dump() == payload


def test_note_entry_rejects_missing_required_field() -> None:
  payload = {
    "name": "compose_blues",
    "domain": "music",
    "signature": "sig",
    "short_desc": "sd",
    # long_desc missing
    "uri": "forge-note:///music/compose_blues",
  }
  with pytest.raises(ValidationError):
    NoteEntry.model_validate(payload)


def test_vault_note_rejects_unknown_state_value() -> None:
  payload = {
    "note_id": "abc123",
    "name": "hello_forge",
    "path": "vault/hello_forge.md",
    "state": "living",  # invalid — not in Literal
    "source_facet": "Recipe",
    "latest_recipe_version": 3,
  }
  with pytest.raises(ValidationError):
    VaultNoteEntry.model_validate(payload)


def test_input_schemas_are_json_serializable_and_typed() -> None:
  # Both inputSchemas are dict-of-dict — sanity check we can serialize them
  # to JSON (the MCP SDK will do this at handshake time).
  import json

  json.dumps(read_note_catalog.INPUT_SCHEMA)
  json.dumps(read_notes_in_vault.INPUT_SCHEMA)
  assert read_note_catalog.INPUT_SCHEMA["type"] == "object"
  assert read_notes_in_vault.INPUT_SCHEMA["type"] == "object"
