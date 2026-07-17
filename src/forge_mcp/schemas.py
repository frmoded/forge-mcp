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
  """A single vault note (user-authored candidate/committed material).

  Drain CW-MCP-2-E — reshaped from the Sprint 1 speculative shape
  (`state`, `source_facet`, `latest_recipe_version`) to the fields the
  local `VaultFS.list_notes` walker can actually populate from note
  frontmatter + facet-presence detection. The dropped fields were
  never populated (the endpoint they proxied to never existed on
  forge-transpile). Richer per-note metadata is a future
  `forge_describe_note` polish drain.
  """

  model_config = ConfigDict(extra="forbid")

  note_id: str = Field(..., description="Stable vault-scoped id for the note (path minus `.md`).")
  name: str = Field(..., description="Human-readable note name (filename stem).")
  path: str = Field(..., description="Vault-relative path to the note file.")
  has_recipe: bool = Field(
    ...,
    description="True iff the note has a `# Recipe` (or legacy `# E--`) facet section.",
  )
  recipe_version: int | None = Field(
    None,
    ge=0,
    description=(
      "The note's `recipe_version` frontmatter stamp; None when the "
      "stamp is absent (never committed via forge_commit_recipe)."
    ),
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
# Run (CW-MCP-2-B) — forge_run_recipe + forge_get_run_result
# -----------------------------------------------------------------------------


class RunArtifactManifest(BaseModel):
  model_config = ConfigDict(extra="forbid")

  name: str = Field(..., description="Filename inside the run's artifact dir.")
  mime_type: str = Field(..., description="Detected mime type (extension-based).")
  size_bytes: int = Field(..., ge=0)
  uri: str = Field(
    ...,
    description="forge-artifact:///{run_id}/{name} — resource URI for on-demand fetch.",
  )


class RunResult(BaseModel):
  """Envelope for forge_run_recipe. Mirrors forge-transpile's RunResponse."""

  model_config = ConfigDict(extra="forbid")

  parse_status: Literal["ok", "parse_error"] = Field(...)
  run_id: str = Field("", description="Empty on parse_error.")
  parse_error: ParseErrorDetail | None = None
  duration_ms: int = 0
  exit_code: int = 0
  timed_out: bool = False
  stdout_preview: str = ""
  artifacts: list[RunArtifactManifest] = Field(default_factory=list)


class GetRunResult(BaseModel):
  """Envelope for forge_get_run_result. Mirrors forge-transpile's GetRunResponse."""

  model_config = ConfigDict(extra="forbid")

  run_id: str
  created_at: int
  expires_at: int
  duration_ms: int
  exit_code: int
  timed_out: bool
  stdout: str
  stderr: str
  artifacts: list[RunArtifactManifest]


# -----------------------------------------------------------------------------
# Commit (CW-MCP-2-C) — forge_commit_recipe result
# -----------------------------------------------------------------------------


class CommitResult(BaseModel):
  """Envelope for forge_commit_recipe.

  Drain CW-MCP-2-C ships the D-B (facet-scoped commit) + D-mcp-3 (agent-
  driven version conflict) surface. `committed_version` is the note's
  new `recipe_version` frontmatter stamp AFTER the write; `run_id` is
  the return of the internal `forge_run_recipe` invocation that
  produces artifacts (D-B contract — commit + run are one operation
  from the agent's perspective).

  `git_sha` is None when the vault isn't git-tracked (per drain §6
  out-of-scope).
  """

  model_config = ConfigDict(extra="forbid")

  note_id: str = Field(..., description="Vault-relative note identifier.")
  committed_version: int = Field(
    ..., ge=1, description="New `recipe_version` stamp on the note."
  )
  run_id: str = Field(
    "", description="Empty when the internal run wasn't invoked (e.g., parse error)."
  )
  git_sha: str | None = Field(
    None, description="SHA of the git commit for this write; None if the vault isn't git-tracked."
  )


# -----------------------------------------------------------------------------
# Multi-vault + create (CW-MCP-multi-vault-create-dir)
# -----------------------------------------------------------------------------


class VaultEntry(BaseModel):
  """A registered vault as returned by forge_list_vaults."""

  model_config = ConfigDict(extra="forbid")

  name: str = Field(..., description="Vault name (from FORGE_VAULTS env).")
  path: str = Field(..., description="Absolute path to the vault root.")
  note_count: int = Field(
    ..., ge=0, description="Number of .md notes discovered in the vault."
  )


class ListVaultsResult(BaseModel):
  """Result envelope for forge_list_vaults."""

  model_config = ConfigDict(extra="forbid")

  vaults: list[VaultEntry] = Field(default_factory=list)


class CreateDirectoryResult(BaseModel):
  """Result envelope for forge_create_directory."""

  model_config = ConfigDict(extra="forbid")

  vault: str = Field(..., description="Vault name the directory was created in.")
  path: str = Field(..., description="Vault-relative path of the created directory.")
  absolute_path: str = Field(..., description="Absolute filesystem path.")


class CreateNoteResult(BaseModel):
  """Result envelope for forge_create_note."""

  model_config = ConfigDict(extra="forbid")

  vault: str = Field(..., description="Vault name the note was created in.")
  note_id: str = Field(..., description="Vault-relative note identifier (stem path).")
  path: str = Field(..., description="Vault-relative path of the created .md file.")
  absolute_path: str = Field(..., description="Absolute filesystem path.")


class NoteContent(BaseModel):
  """Full V2a content of a single vault note.

  CW-MCP-read-note. Returned by forge_read_note. Missing facets
  surface as None (Recipe/Python/Data) or empty (Description); the
  raw field always carries the original markdown so agents can
  reconstruct any structure the parser doesn't expose."""

  model_config = ConfigDict(extra="forbid")

  note_id: str = Field(..., description="Vault-relative note identifier.")
  vault: str = Field(..., description="Vault name the note lives in.")
  frontmatter: dict[str, str] = Field(
    default_factory=dict,
    description=(
      "Parsed YAML frontmatter (shallow — scalar values only). Lists / "
      "nested objects appear as their raw string form."
    ),
  )
  description: str = Field(
    "", description="Body under `# Description`; empty when absent."
  )
  recipe: str | None = Field(
    None, description="Body under `# Recipe` (or legacy `# E--`); None when absent."
  )
  python: str | None = Field(
    None, description="Body under `# Python`; None when absent."
  )
  data: str | None = Field(
    None,
    description=(
      "Body under `# Data` as raw text; None when absent. Callers parse "
      "the mimetype (typically YAML) themselves."
    ),
  )
  inputs: list[str] = Field(
    default_factory=list,
    description=(
      "Declared inputs — from frontmatter `inputs: [x, y]` (canonical) "
      "or a `Inputs: x, y` line in Description (legacy). Empty if none."
    ),
  )
  raw: str = Field(
    ..., description="Full markdown source, verbatim."
  )


class ReadNoteResult(BaseModel):
  """Result envelope for forge_read_note."""

  model_config = ConfigDict(extra="forbid")

  note: NoteContent = Field(..., description="Parsed content of the requested note.")


class RegisterVaultResult(BaseModel):
  """Result envelope for forge_register_vault (runtime registration).

  CW-MCP-runtime-vault-registration."""

  model_config = ConfigDict(extra="forbid")

  registered_vault: VaultEntry = Field(
    ..., description="The vault that was just added to the live registry."
  )


class UnregisterVaultResult(BaseModel):
  """Result envelope for forge_unregister_vault."""

  model_config = ConfigDict(extra="forbid")

  unregistered: bool = Field(
    ..., description="True on success; false on isError paths (populated for schema symmetry)."
  )
  remaining_vaults: list[str] = Field(
    default_factory=list,
    description="Names of the vaults still registered after the removal.",
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
