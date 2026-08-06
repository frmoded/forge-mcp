"""`forge_read_notes_in_vault` — list vault notes.

Drain CW-MCP-2-E — LOCAL VaultFS-backed handler (Option C, same
architecture as CW-MCP-2-C's `forge_commit_recipe`). Pre-drain this
tool proxied `forge-transpile /vault/notes`, an endpoint that has
NEVER been implemented; every call silently returned an "endpoint
missing" isError. Now it walks the same local vault that
`forge_commit_recipe` writes to → symmetric read/write surface,
single source of truth.

Wire spec: `forge-mcp-tool-surface-v1.md` §Reading — reshaped from the
Sprint 1 speculative fields (`state`, `source_facet`,
`latest_recipe_version`) to the fields the local walker can actually
populate. Richer per-note metadata (state / source_facet computation)
is deferred to a future `forge_describe_note` polish drain.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from ..schemas import VaultListResult, VaultNoteEntry
from ..vault_fs import VaultFS, VaultFSError
from ..vault_registry import VaultNotFoundError, VaultRegistry

TOOL_NAME = "forge_read_notes_in_vault"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "properties": {
    "filter": {
      "type": "string",
      "description": "Optional substring filter on note_id (case-sensitive).",
    },
    "vault": {
      "type": "string",
      "description": (
        "Vault name (from forge_list_vaults). Optional — defaults to "
        "the first-registered vault."
      ),
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["notes"],
  "properties": {
    "notes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["note_id", "name", "path", "has_recipe", "source_vault"],
        "properties": {
          "note_id": {"type": "string"},
          "name": {"type": "string"},
          "path": {"type": "string"},
          "has_recipe": {"type": "boolean"},
          "recipe_version": {"type": ["integer", "null"], "minimum": 0},
          "type": {"type": "string", "enum": ["action", "data", "vanilla"]},
          "source_vault": {"type": "string"},
          "collides_with": {"type": "array", "items": {"type": "string"}},
        },
      },
    }
  },
}

DESCRIPTION = (
  "List vault notes. Walks the vault directory and returns one entry "
  "per `.md` file with `{note_id, name, path, has_recipe, "
  "recipe_version, source_vault}`. When the vault's forge.toml declares "
  "[imports], imported vaults' notes are appended after the local ones "
  "in declaration order, each tagged with the import NAME as "
  "source_vault; a `collides_with` list flags bare-name twins across "
  "sources. Optional `filter` argument does a substring match on "
  "note_id. Hidden dirs (`.obsidian/`, `.git/`, etc.) are excluded. "
  "Symmetric with forge_commit_recipe — both read/write the same vault."
)


def _vault_root_from_env() -> Path:
  """Read `FORGE_VAULT_PATH` from the environment (default:
  `~/forge-vaults/bluh`). Same env-var convention as
  `commit_recipe._vault_root_from_env` so the two tools stay in sync."""
  raw = os.environ.get("FORGE_VAULT_PATH", "~/forge-vaults/bluh").strip()
  return Path(raw).expanduser()


def _vault_name_from_manifest(vault_fs: VaultFS) -> str:
  """Best-effort vault name for the registry-less seams (injected
  `vault_fs` / FORGE_VAULT_PATH): the forge.toml `name` when readable,
  else the root directory's basename. Registry callers never reach
  this — their name is the registration key."""
  manifest = vault_fs.root / "forge.toml"
  if manifest.exists():
    try:
      name = tomllib.loads(manifest.read_text(encoding="utf-8")).get("name")
      if isinstance(name, str) and name.strip():
        return name.strip()
    except (tomllib.TOMLDecodeError, OSError):
      pass  # fall through — a bad manifest shouldn't break a read-only listing
  return vault_fs.root.name


def _tag_and_flag(
  groups: list[tuple[str, list[dict]]],
) -> list[VaultNoteEntry]:
  """Tag each raw walker entry with its source and flag cross-source
  bare-name twins.

  `groups` is ordered: local first, then imports in `[imports]`
  declaration order. The collision unit is the bare name (`name`
  field) — that is what bare wikilinks resolve by (see
  vault_link_resolver's bare-form search), so it is the granularity at
  which a caller needs disambiguation. Same-source internal twins are
  the engine collision guard's territory, not this listing's.
  """
  name_sources: dict[str, list[str]] = {}
  for source, raw in groups:
    for entry in raw:
      name_sources.setdefault(entry["name"], []).append(source)

  notes: list[VaultNoteEntry] = []
  for source, raw in groups:
    for entry in raw:
      seen: set[str] = set()
      others: list[str] = []
      for s in name_sources[entry["name"]]:
        if s != source and s not in seen:
          seen.add(s)
          others.append(s)
      notes.append(
        VaultNoteEntry.model_validate(
          {**entry, "source_vault": source, "collides_with": others}
        )
      )
  return notes


def _summary_text(notes: list[VaultNoteEntry], filter_: str | None) -> str:
  scope = f"filter '{filter_}'" if filter_ else "no filter"
  if not notes:
    return f"No vault notes matched ({scope})."
  return f"Found {len(notes)} vault note(s) matching {scope}."


async def run(
  arguments: dict[str, Any],
  bearer: str,  # noqa: ARG001 — kept for wrapper-signature symmetry with other tools
  vault_fs: VaultFS | None = None,
  vault_registry: VaultRegistry | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns the MCP tool-result shape.

  `vault_fs` param is a dependency-injection seam for tests; production
  callers pass None → constructed from env. `bearer` is kept in the
  signature so the FastMCP wrapper's call site stays symmetric with the
  other tools (see server.py::_forge_read_notes_in_vault), but the
  local read path doesn't need it — no upstream forge-transpile call.

  `vault_registry` supersedes `vault_fs` for multi-vault dispatch
  (CW-MCP-multi-vault-create-dir); the caller passes it in and
  `arguments['vault']` is resolved through the registry.
  """
  filter_ = arguments.get("filter")
  vault_name = arguments.get("vault")

  # Vault-split 3d (drain 2026-08-06-0200): every entry carries
  # source_vault. Imports exist only in the registry (it is the sole
  # parser/owner of [imports] roots — drain 1900 3b), so the injected-
  # vault_fs and FORGE_VAULT_PATH seams list local notes only.
  import_roots: dict[str, VaultFS] = {}
  if vault_fs is None:
    if vault_registry is not None:
      try:
        vault_fs = vault_registry.get(vault_name)
      except VaultNotFoundError as exc:
        return {
          "content": [{"type": "text", "text": str(exc)}],
          "structuredContent": {"notes": []},
          "isError": True,
        }
      local_name = vault_name if vault_name else vault_registry.names()[0]
      import_roots = vault_registry.get_import_roots(vault_name)
    else:
      try:
        vault_fs = VaultFS(root=_vault_root_from_env())
      except VaultFSError as exc:
        return {
          "content": [
            {
              "type": "text",
              "text": (
                f"Vault filesystem unavailable: {exc}. Set FORGE_VAULT_PATH "
                "to an existing vault directory in the forge-mcp environment."
              ),
            }
          ],
          "structuredContent": {"notes": []},
          "isError": True,
        }
      local_name = _vault_name_from_manifest(vault_fs)
  else:
    local_name = _vault_name_from_manifest(vault_fs)

  groups: list[tuple[str, list[dict]]] = [
    (local_name, vault_fs.list_notes(filter=filter_))
  ]
  for import_name, import_fs in import_roots.items():
    groups.append((import_name, import_fs.list_notes(filter=filter_)))

  notes = _tag_and_flag(groups)
  result = VaultListResult(notes=notes)
  return {
    "content": [{"type": "text", "text": _summary_text(notes, filter_)}],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }
