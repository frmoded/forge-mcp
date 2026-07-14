"""Pydantic v2 models for the read-only tool surface.

These are the wire types forge-MCP promises callers. They double as:
  - The `outputSchema` source of truth (via `.model_json_schema()`).
  - The parse/validate layer against forge-transpile responses.

Refer to `forge-mcp-tool-surface-v1.md` §Reading/catalog for the spec.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# -----------------------------------------------------------------------------
# Library-catalog note (returned by forge_read_note_catalog)
# -----------------------------------------------------------------------------


class NoteEntry(BaseModel):
  """A single library note as returned by the catalog tool."""

  model_config = ConfigDict(extra="forbid")

  name: str = Field(..., description="Note name (e.g. 'compose_blues').")
  domain: str = Field(..., description="Domain name (e.g. 'music', 'moda').")
  signature: str = Field(
    ...,
    description=(
      "E-- signature: `Call [[name]] with param1=type1, param2=type2` returning type"
    ),
  )
  short_desc: str = Field(..., description="One-line description, <=120 chars.")
  long_desc: str = Field(
    ...,
    description="Paragraph description including usage guidance and typical compositions.",
  )
  uri: str = Field(
    ...,
    description="forge-note:///{domain}/{name} — stable identifier + future extension point.",
  )


class NoteCatalogResult(BaseModel):
  """Result envelope for forge_read_note_catalog."""

  model_config = ConfigDict(extra="forbid")

  notes: list[NoteEntry] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Vault note (returned by forge_read_notes_in_vault)
# -----------------------------------------------------------------------------

# V2 vocabulary per Forge constitution §S9 (drain 2026-07-09-1600 renamed
# `canonical_facet` → `source_facet`; drain 2026-07-12-1335 aligned this
# schema to the constitutional vocabulary). All four values are the
# tokens the plugin actually writes into note frontmatter.
VaultSourceFacet = Literal["description", "recipe", "python", "synced"]

# `state` is intentionally NOT a Literal. The pre-drain guess
# `{draft, committed, archived}` was agent-authoring lifecycle concepts,
# not vault-native ones. Constitution §S9's hexa-state suffix set
# (`— source`, `— derived from Description`, `— derived from Recipe`,
# `— derived from Description, out of date`, `— derived from Recipe,
# out of date`, `— ignored`) is a per-facet suffix, not a note-level
# label — mapping it onto a single `state` field would double-encode
# `source_facet`. Per drain 1335 §4 fallback, keep `state: str` open
# until Sprint 2 vault-integration work decides the shape (or replaces
# the field with a `{has_recipe, has_python, has_description}` bag).


class VaultNoteEntry(BaseModel):
  """A single vault note (user-authored candidate/committed material)."""

  model_config = ConfigDict(extra="forbid")

  note_id: str = Field(..., description="Stable vault-scoped id for the note.")
  name: str = Field(..., description="Human-readable note name.")
  path: str = Field(..., description="Vault-relative path to the note file.")
  state: str = Field(
    ...,
    description=(
      "Vault-native state label. Left open (any string) until Sprint 2 "
      "picks a concrete shape; see drain 1335 FEEDBACK §Fallback."
    ),
  )
  source_facet: VaultSourceFacet = Field(
    ...,
    description=(
      "Which facet currently holds the compilable source per Forge "
      "constitution §S9: description | recipe | python | synced."
    ),
  )
  latest_recipe_version: int = Field(
    ...,
    ge=0,
    description="Highest committed Recipe version on the note; 0 if never committed.",
  )


class VaultListResult(BaseModel):
  """Result envelope for forge_read_notes_in_vault."""

  model_config = ConfigDict(extra="forbid")

  notes: list[VaultNoteEntry] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Compile (CW-MCP-2-A) — forge_compile_recipe result
# -----------------------------------------------------------------------------


class ParseErrorDetail(BaseModel):
  """Structured parse-error payload from forge-transpile /compile.

  Mirrors forge-transpile's ParseErrorDetail model verbatim so the
  wire shape stays symmetric. All fields present even when the parser
  didn't surface them (line/column may be 0; expected may be "").
  """

  model_config = ConfigDict(extra="forbid")

  line: int = Field(..., ge=0, description="1-based line number; 0 = unknown.")
  column: int = Field(..., ge=0, description="1-based column number; 0 = unknown.")
  message: str = Field(..., description="Human-readable parse error.")
  expected: str = Field(
    "",
    description="One-line example of what would have parsed; empty if unavailable.",
  )


class CompileResult(BaseModel):
  """Result envelope for forge_compile_recipe.

  On parse success: `parse_status="ok"` + `python_source` populated +
  `unresolved_slot_count` reflects how many `{{ ... }}` placeholders
  survived (agents call `/resolve-slot` before `/run` if > 0).

  On parse failure: `parse_status="parse_error"` + `parse_error`
  populated. HTTP is still 200 — parse errors are business errors,
  not protocol errors.
  """

  model_config = ConfigDict(extra="forbid")

  parse_status: Literal["ok", "parse_error"] = Field(..., description="'ok' | 'parse_error'")
  python_source: str | None = Field(
    None, description="Compiled Python source; None on parse_error."
  )
  unresolved_slot_count: int = Field(
    0, ge=0, description="Number of unresolved `{{ ... }}` slots."
  )
  parse_error: ParseErrorDetail | None = Field(
    None, description="Structured parse error; None on success."
  )


# -----------------------------------------------------------------------------
# Error envelope
# -----------------------------------------------------------------------------


class ForgeMcpError(BaseModel):
  """Structured error surface for `isError: true` responses.

  Follows the three-part convention: what went wrong / what was expected /
  a one-line example that would have worked.
  """

  model_config = ConfigDict(extra="forbid")

  kind: str = Field(
    ...,
    description="Machine-readable error kind (e.g. 'not_found', 'endpoint_missing').",
  )
  message: str = Field(..., description="Human-readable error message.")
  expected: str | None = Field(
    default=None,
    description="One-line example of what would have worked.",
  )
