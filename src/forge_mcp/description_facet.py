"""Description-facet rewrite for action notes (drain 2026-07-31-1350).

Replaces the contents of an action note's `# Description` block while
leaving frontmatter, `# Recipe`, `# Python` and any trailing facet
byte-for-byte intact.

Why this is a separate module
-----------------------------
It is a pure string transform over a known line-oriented shape, so it
tests without a vault, a git repo, or an MCP client. The tool that
calls it stays a thin dispatch.

On NOT stamping hexa-state
--------------------------
This deliberately does not touch `source_facet`, `sync_state`, or any
`*_hash` / `*_derived_from_*` field. See the drain FEEDBACK for the
full argument; the short version is that forge-mcp has no stamp writer
to reuse (`commit_recipe` bumps `recipe_version` and splices the Recipe
body, nothing more), and the plugin already stamps reactively on edit
and on file-open. Writing stamps here would mean reimplementing
`facet-hash-core.ts`'s SHA-256 facet hashing in Python and keeping the
two in lockstep forever — and it would make the MCP path DIVERGE from a
hand-edit rather than match it, since a driver typing in Obsidian
doesn't stamp anything either. The plugin does, afterwards.
"""

from __future__ import annotations

import re

__all__ = ["rewrite_description_facet", "DescriptionFacetError"]


class DescriptionFacetError(ValueError):
  """The note doesn't have the `# Description` shape this can rewrite."""


# Facet headings are line-oriented and exactly `# <Name>` — mirrors
# vault_fs._FACET_HEADERS. `# E--` is a legacy facet still found in
# older notes; it counts as a boundary so a Description rewrite can
# never swallow one.
_FACET_HEADING = re.compile(r"^# (?:Description|Recipe|Python|E--)\s*$", re.M)
_DESCRIPTION_HEADING = re.compile(r"^# Description[ \t]*$", re.M)


def rewrite_description_facet(existing_body: str, new_description: str) -> str:
  """Return `existing_body` with the `# Description` block replaced.

  Everything before the heading (frontmatter, any preamble) and
  everything from the next facet heading onward is preserved exactly —
  including the note's original line endings around those regions.

  Raises DescriptionFacetError when there is no `# Description`
  heading, or more than one. Both mean the caller's assumption about
  the note's shape is wrong, and silently guessing which block to
  rewrite is worse than refusing.
  """
  headings = list(_DESCRIPTION_HEADING.finditer(existing_body))
  if not headings:
    raise DescriptionFacetError(
      "note has no `# Description` heading — cannot rewrite that facet"
    )
  if len(headings) > 1:
    raise DescriptionFacetError(
      f"note has {len(headings)} `# Description` headings; refusing to "
      "guess which one to rewrite"
    )

  head = headings[0]
  # The block runs from just after the heading line to the next facet
  # heading, or EOF.
  next_facet = None
  for m in _FACET_HEADING.finditer(existing_body, head.end()):
    next_facet = m
    break

  prefix = existing_body[: head.end()]
  suffix = existing_body[next_facet.start():] if next_facet else ""

  body = new_description.strip("\n")
  if suffix:
    # Blank line above the following heading, matching the shape
    # commit_recipe writes for fresh notes.
    return f"{prefix}\n\n{body}\n\n{suffix}" if body else f"{prefix}\n\n\n{suffix}"
  # Description is the trailing facet.
  return f"{prefix}\n\n{body}\n" if body else f"{prefix}\n\n"
