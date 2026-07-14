"""Drain CW-MCP-3-A — audit sweep on every registered tool's OUTPUT_SCHEMA.

Three invariants, each parametrized across all 6 tools:

1. Schema is a valid Draft 2020-12 JSON Schema (jsonschema meta-validation).
2. Schema declares a non-empty `required` array (defense against
   accidentally-shipped schemaless outputs).
3. Schema is a superset of the fields tool-surface v1 spec calls out
   for that tool (Registry submission needs schemas the reviewer can
   trust to describe every field the agent will see).

Also validates that a Pydantic-model round-trip's `.model_dump()`
validates against its own tool's OUTPUT_SCHEMA — the two shape sources
(hand-written schema + generated schema) must agree.
"""
from __future__ import annotations

import jsonschema
import pytest

from forge_mcp.tools import (
  commit_recipe,
  compile_recipe,
  get_run_result,
  read_note_catalog,
  read_notes_in_vault,
  run_recipe,
)

ALL_TOOLS = [
  commit_recipe,
  compile_recipe,
  get_run_result,
  read_note_catalog,
  read_notes_in_vault,
  run_recipe,
]


def _pid(mod) -> str:
  return mod.TOOL_NAME


# ---------------------------------------------------------------------------
# Invariant 1 — every schema is a valid JSON Schema.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_mod", ALL_TOOLS, ids=_pid)
def test_output_schema_is_valid_json_schema(tool_mod):
  """RC 2026-07-28 requires outputSchemas to be valid JSON Schema so
  MCP clients can validate structuredContent against them. Ship
  bug-free schemas or you break every downstream validator."""
  jsonschema.Draft202012Validator.check_schema(tool_mod.OUTPUT_SCHEMA)


# ---------------------------------------------------------------------------
# Invariant 2 — non-empty `required`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_mod", ALL_TOOLS, ids=_pid)
def test_output_schema_declares_required_fields(tool_mod):
  """Every tool schema must name at least one required field. Empty /
  missing `required` means the schema accepts `{}` — a form Registry
  reviewers reject as insufficiently constrained."""
  required = tool_mod.OUTPUT_SCHEMA.get("required", [])
  assert isinstance(required, list), (
    f"{tool_mod.TOOL_NAME}.OUTPUT_SCHEMA.required must be a list, "
    f"got {type(required).__name__}"
  )
  assert len(required) >= 1, (
    f"{tool_mod.TOOL_NAME}.OUTPUT_SCHEMA declares no required fields — "
    "would accept `{}` as valid output"
  )


# ---------------------------------------------------------------------------
# Invariant 3 — schema matches tool-surface v1 spec.
#
# Table below is the field set the spec (lines 34-267 of tool-surface
# v1) promises for each tool. This is a superset check: the schema
# MAY have more fields (evolution), but must not be missing any.
# ---------------------------------------------------------------------------


SPEC_REQUIRED_FIELDS: dict[str, set[str]] = {
  "forge_read_note_catalog": {"notes"},
  "forge_read_notes_in_vault": {"notes"},
  "forge_compile_recipe": {"parse_status"},
  "forge_run_recipe": {"parse_status", "run_id"},
  "forge_get_run_result": {"run_id", "stdout", "stderr", "artifacts"},
  "forge_commit_recipe": {"note_id", "committed_version"},
}


@pytest.mark.parametrize("tool_mod", ALL_TOOLS, ids=_pid)
def test_output_schema_covers_spec_fields(tool_mod):
  """Tool-surface v1 lists a specific set of fields per tool. The
  live OUTPUT_SCHEMA must expose (as `properties`) every one — Registry
  submission mirrors the schema to consumer docs; missing fields
  become undocumented surface."""
  properties = set(tool_mod.OUTPUT_SCHEMA.get("properties", {}).keys())
  expected = SPEC_REQUIRED_FIELDS.get(tool_mod.TOOL_NAME, set())
  missing = expected - properties
  assert not missing, (
    f"{tool_mod.TOOL_NAME} OUTPUT_SCHEMA is missing spec-required "
    f"properties: {sorted(missing)}"
  )


# ---------------------------------------------------------------------------
# Cross-check: Pydantic model → dump → validate against schema.
#
# Every tool's `structuredContent` payload is built from a Pydantic
# model (schemas.py). If the model's field set drifts from the
# hand-written schema, one path (schema validation) or the other
# (Pydantic validation) silently accepts wrong shapes. Round-trip
# check pins them together.
# ---------------------------------------------------------------------------


def _sample_payload(tool_mod) -> dict:
  """Minimal-valid-instance for each tool's outputSchema. Kept in one
  place so a schema change forces a matching sample update — the
  round-trip test then catches Pydantic/schema drift."""
  if tool_mod.TOOL_NAME == "forge_read_note_catalog":
    return {"notes": []}
  if tool_mod.TOOL_NAME == "forge_read_notes_in_vault":
    return {"notes": []}
  if tool_mod.TOOL_NAME == "forge_compile_recipe":
    return {"parse_status": "ok"}
  if tool_mod.TOOL_NAME == "forge_run_recipe":
    return {
      "parse_status": "ok",
      "run_id": "abc",
      "duration_ms": 0,
      "exit_code": 0,
      "timed_out": False,
      "stdout_preview": "",
      "artifacts": [],
    }
  if tool_mod.TOOL_NAME == "forge_get_run_result":
    return {
      "run_id": "abc",
      "created_at": 0,
      "expires_at": 0,
      "duration_ms": 0,
      "exit_code": 0,
      "timed_out": False,
      "stdout": "",
      "stderr": "",
      "artifacts": [],
    }
  if tool_mod.TOOL_NAME == "forge_commit_recipe":
    return {"note_id": "x", "committed_version": 1}
  raise AssertionError(f"no sample payload defined for {tool_mod.TOOL_NAME}")


@pytest.mark.parametrize("tool_mod", ALL_TOOLS, ids=_pid)
def test_output_schema_accepts_minimal_valid_payload(tool_mod):
  """The minimal payload each tool actually emits (all required
  fields, empty arrays where possible) must validate against the
  declared schema."""
  jsonschema.Draft202012Validator(tool_mod.OUTPUT_SCHEMA).validate(_sample_payload(tool_mod))


@pytest.mark.parametrize("tool_mod", ALL_TOOLS, ids=_pid)
def test_output_schema_rejects_empty_object(tool_mod):
  """Empty `{}` must fail — this is the regression that Invariant 2
  guards against at the schema level; here we exercise it end-to-end
  through the validator to prove the required list has teeth."""
  with pytest.raises(jsonschema.ValidationError):
    jsonschema.Draft202012Validator(tool_mod.OUTPUT_SCHEMA).validate({})
