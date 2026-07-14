"""`forge_commit_recipe` — persist Recipe facet to a vault note.

CW-MCP-2-C. Option C architecture (see drain §4 §Architecture Question):
forge-mcp writes directly to the local vault filesystem — no round-trip
through forge-transpile — because forge-transpile is a stateless HTTP
service on EC2 with no vault-fs access. forge-mcp IS local, is the
same trust boundary as the vault, and can splice the Recipe facet
in-process.

Wire spec: `forge-mcp-tool-surface-v1.md` §Compile / run / commit →
`forge_commit_recipe` lines 174-202.

Adopted design decisions:
- D-B (facet-scoped commit): Description + Python + frontmatter are
  preserved byte-for-byte; only the Recipe facet body is replaced.
- D-mcp-3 (agent-driven version conflict): on stale write, return
  isError with actionable text naming expected + current versions.
  Agent retries after fetching. No merge logic here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..forge_service_client import ForgeServiceClient
from ..schemas import CommitResult
from ..vault_fs import (
  NoteIdInvalid,
  NoteNotFound,
  VaultFS,
  VaultFSError,
  VersionConflict,
)
from . import run_recipe

TOOL_NAME = "forge_commit_recipe"

INPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["source", "note_id"],
  "properties": {
    "source": {
      "type": "string",
      "description": "E-- Recipe source text to persist as the note's Recipe facet.",
    },
    "note_id": {
      "type": "string",
      "description": (
        "Vault-relative note identifier (e.g. `mcp-scratch/agent_test`). "
        "Trailing `.md` optional. Path-traversal patterns (`..`, hidden "
        "segments, absolute paths) are rejected."
      ),
    },
    "expected_version": {
      "type": ["integer", "null"],
      "description": (
        "The `recipe_version` the agent last saw. When set, the commit "
        "fails with a version-conflict error if the note has been "
        "modified since. Omit / null on first commit to a fresh note."
      ),
      "minimum": 0,
    },
  },
}

OUTPUT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "required": ["note_id", "committed_version"],
  "properties": {
    "note_id": {"type": "string"},
    "committed_version": {"type": "integer", "minimum": 1},
    "run_id": {"type": "string"},
    "git_sha": {"type": ["string", "null"]},
  },
}

DESCRIPTION = (
  "Persist a Recipe to a vault note's Recipe facet. Preserves the "
  "Description + Python + frontmatter byte-for-byte. Returns the new "
  "`committed_version` (frontmatter `recipe_version` bump) + a `run_id` "
  "from an internal run + a `git_sha` (null if the vault isn't git-"
  "tracked). Agents SHOULD pass `expected_version` (the version they "
  "last saw via forge_read_notes_in_vault) to detect stale writes; a "
  "conflict returns isError:true with actionable text naming the "
  "expected + current versions — the agent re-reads and retries."
)


def _vault_root_from_env() -> Path:
  """Read `FORGE_VAULT_PATH` from the environment. Default is the driver's
  bluh vault at `~/forge-vaults/bluh/`. Raises VaultFSError if the resolved
  path isn't a directory (the VaultFS constructor validates that)."""
  raw = os.environ.get("FORGE_VAULT_PATH", "~/forge-vaults/bluh").strip()
  return Path(raw).expanduser()


async def run(
  arguments: dict[str, Any],
  bearer: str,
  client: ForgeServiceClient | None = None,
  vault_fs: VaultFS | None = None,
) -> dict[str, Any]:
  """Execute the tool. Returns the MCP tool-result shape.

  On success: `isError: false`, `structuredContent = CommitResult(...)`,
  text summarizes "Committed <note_id> v<N>; run_id=<...>".

  On version conflict: `isError: true`, text names expected + current
  versions per D-mcp-3.

  On invalid note_id / write-failure: `isError: true` with actionable
  text.

  `vault_fs` param is a dependency injection seam for tests (per drain
  §5 tests). Production callers pass None → construct from env.
  """
  source = arguments.get("source")
  note_id = arguments.get("note_id")
  expected_version = arguments.get("expected_version")

  # Drain §5 test #5 — missing/malformed note_id (tool-input validation
  # BEFORE any fs touching).
  if not isinstance(source, str) or not source:
    return _error(
      "Missing required argument: 'source' (E-- Recipe text).",
      note_id=str(note_id or ""),
    )
  if not isinstance(note_id, str) or not note_id:
    return _error(
      "Missing required argument: 'note_id' (vault-relative path).",
      note_id="",
    )
  if expected_version is not None and not isinstance(expected_version, int):
    return _error(
      "'expected_version' must be an integer or omitted.",
      note_id=note_id,
    )

  # Construct VaultFS from env unless the caller injected one. Env-based
  # errors (path missing) surface as isError so the driver sees a clean
  # message instead of a 500.
  if vault_fs is None:
    try:
      vault_fs = VaultFS(root=_vault_root_from_env())
    except VaultFSError as exc:
      return _error(
        f"Vault filesystem unavailable: {exc}. Set FORGE_VAULT_PATH to "
        "an existing vault directory in the forge-mcp environment.",
        note_id=note_id,
      )

  # Drain §5 test #6 — traversal defense fires HERE.
  try:
    # Let vault_fs generate the default git message — it includes the
    # `v{new_version}` suffix that `read_recipe_version` grep-matches
    # against when resolving `forge-recipe:///{note_id}/v{n}`. Passing a
    # custom message would break the resource resolver's convention.
    new_version, git_sha = vault_fs.commit_recipe(
      note_id=note_id,
      new_recipe_body=source,
      expected_version=expected_version,
    )
  except NoteIdInvalid as exc:
    return _error(f"Invalid note_id: {exc}", note_id=note_id)
  except NoteNotFound as exc:
    # Only fires when the caller pre-checked existence and then the
    # note vanished; `commit_recipe` creates fresh notes so this is
    # rare. Still surface cleanly.
    return _error(str(exc), note_id=note_id)
  except VersionConflict as exc:
    return _error(
      f"{exc} Fetch again via forge_read_notes_in_vault + retry.",
      note_id=note_id,
      structured={
        "note_id": note_id,
        "committed_version": exc.current,
        "run_id": "",
        "git_sha": None,
      },
    )
  except VaultFSError as exc:
    return _error(f"Vault write failed: {exc}", note_id=note_id)

  # Drain §5 test #4 — commit path invokes forge_run_recipe internally
  # so the caller gets a `run_id` for artifact fetching. Best-effort:
  # if the run fails (upstream 5xx, parse error), we still count the
  # commit as successful — the Recipe body IS in the vault. Surface the
  # run_id or empty string.
  run_id = ""
  try:
    run_result = await run_recipe.run(
      arguments={"source": source},
      bearer=bearer,
      client=client,
    )
    struct = run_result.get("structuredContent") or {}
    if isinstance(struct, dict):
      run_id = str(struct.get("run_id") or "")
  except Exception:  # noqa: BLE001 — best-effort, commit already landed
    run_id = ""

  result = CommitResult(
    note_id=note_id,
    committed_version=new_version,
    run_id=run_id,
    git_sha=git_sha,
  )
  git_hint = f" git={git_sha[:8]}" if git_sha else ""
  return {
    "content": [
      {
        "type": "text",
        "text": (
          f"Committed {note_id} v{new_version}"
          f"{' + run ' + run_id[:8] if run_id else ''}"
          f"{git_hint}."
        ),
      }
    ],
    "structuredContent": result.model_dump(mode="json"),
    "isError": False,
  }


def _error(text: str, *, note_id: str, structured: dict[str, Any] | None = None) -> dict[str, Any]:
  """Convenience: MCP tool-result shape for isError=True."""
  return {
    "content": [{"type": "text", "text": text}],
    "structuredContent": structured or {
      "note_id": note_id,
      "committed_version": 0,
      "run_id": "",
      "git_sha": None,
    },
    "isError": True,
  }
