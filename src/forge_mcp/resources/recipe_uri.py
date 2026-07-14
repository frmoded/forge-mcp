"""`forge-recipe:///{note_id}/v{n}` — versioned Recipe artifact resource.

Drain CW-MCP-2-C companion to `forge_commit_recipe`. Fetches the Recipe
body as it was at a specific `recipe_version` stamp. Backed by git-log
of the vault (see `vault_fs.read_recipe_version` for the strategy).

Returns text content (Recipe source, `text/plain; charset=utf-8`) —
Recipe bodies are always text. When the vault isn't git-tracked, or
the requested version doesn't exist, returns a clean not-found result
rather than raising — MCP clients render the "history unavailable"
text so the agent can adjust.
"""
from __future__ import annotations

import re

from ..vault_fs import NoteIdInvalid, VaultFS

# `forge-recipe:///{note_id}/v{n}` — split on the final `/v<digits>`
# suffix (note_id may itself contain `/`). Rejects malformed URIs.
_URI_RE = re.compile(r"^forge-recipe:///(?P<note_id>.+)/v(?P<version>\d+)$")


class RecipeUriError(Exception):
  """Malformed forge-recipe:/// URI."""


def parse_forge_recipe_uri(uri: str) -> tuple[str, int]:
  """Return `(note_id, version)` from a `forge-recipe:///{id}/v{n}` URI.
  Raises RecipeUriError on shape violations."""
  m = _URI_RE.match(uri)
  if not m:
    raise RecipeUriError(
      f"Malformed forge-recipe URI {uri!r}; expected "
      "'forge-recipe:///{note_id}/v{version}'"
    )
  note_id = m.group("note_id")
  version = int(m.group("version"))
  return note_id, version


def read_recipe_resource(uri: str, vault_fs: VaultFS) -> dict:
  """Fetch the Recipe body at the requested version.

  Returns an MCP-resource-read shape:

      {"contents": [
          {"uri": <same>, "mimeType": "text/plain", "text": <body>}
      ]}

  On not-found (vault has no git history, OR the version stamp doesn't
  match any commit), returns a contents block with a "history
  unavailable" text so the agent sees the actionable message rather
  than a protocol-level 404.
  """
  try:
    note_id, version = parse_forge_recipe_uri(uri)
  except RecipeUriError as exc:
    return {
      "contents": [
        {"uri": uri, "mimeType": "text/plain", "text": str(exc)}
      ]
    }

  try:
    body = vault_fs.read_recipe_version(note_id, version)
  except NoteIdInvalid as exc:
    return {
      "contents": [
        {"uri": uri, "mimeType": "text/plain", "text": f"Invalid note_id: {exc}"}
      ]
    }

  if body is None:
    return {
      "contents": [
        {
          "uri": uri,
          "mimeType": "text/plain",
          "text": (
            f"No Recipe history available for {note_id!r} at version "
            f"v{version}. The vault may not be git-tracked, or the "
            "version was never committed."
          ),
        }
      ]
    }

  return {
    "contents": [
      {"uri": uri, "mimeType": "text/plain", "text": body}
    ]
  }
