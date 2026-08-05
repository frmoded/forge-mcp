"""Wikilink extraction + vault-note closure walker.

CW-forge-run-recipe-vault-note-invocation-arch-b-pivot
(drain 2026-07-27-1400).

For a user's Recipe source that references `[[vault_note_name]]`,
build the transitive closure of vault-note dependencies + package
each as `{name, recipe_source, inputs}` for the /run vault_notes
payload.

Assumptions:
  - `[[name]]` follows the parser's wikilink grammar (see
    engine_libs/recipe/parser.py:274-303): first char alpha or `_`,
    subsequent alphanumeric + `_` / `/` / `-`.
  - When a wikilink is `[[foo]]` we look up `foo.md` in the vault
    (root-level). When it's `[[dir/foo]]` we look up `dir/foo.md`.
    Both shapes compile to the leaf basename in the transpiler
    (`_render_chip_name` at transpiler.py strips the path prefix), so
    the vault-note wrapper's Python name is always the leaf basename.
  - A wikilink whose target isn't in the vault is silently skipped —
    it's assumed to be an engine chip. Runtime resolution surfaces a
    clean NameError with a chip-preview list from `_Context.compute`.
  - Vanilla / data notes (no `type: action` frontmatter) are also
    skipped — they have no Recipe to transpile.
  - Cycle detection fires when the walker revisits a note already on
    the current descent path. Raises `CircularVaultNoteError`.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .vault_fs import VaultFS


# Matches the parser's wikilink grammar exactly (parser.py:293-299):
#   [[  <alpha|_>  (<alphanum|_|/|->)*  ]]
# Drain 2026-08-05-0710 — the optional `name:` prefix is the
# namespaced form from forge/docs/specs/vault-imports.md:
# `[[music-core:scale]]` names which vault a link means, and
# `[[local:scale]]` names the containing one.
#
# ADDITIVE. The prefix group is optional, so every existing bare
# `[[note-id]]` matches exactly as before and `extract_wikilinks`
# returns the same strings for the same inputs. Nothing downstream has
# to learn about namespaces until it wants to.
_WIKILINK_RE = re.compile(
  r"\[\[(?:([A-Za-z_][A-Za-z0-9_-]*):)?([A-Za-z_][A-Za-z0-9_/-]*)\]\]"
)


class CircularVaultNoteError(Exception):
  """Raised when the vault-note closure walker detects a cycle.

  Message names the cycle path (chain of note names) so callers can
  surface it to the driver / wizard for fix-up.
  """


def extract_wikilinks(source: str) -> list[str]:
  """Return the ordered list of unique wikilink names in `source`.

  Order = first-appearance order. Uniqueness = de-dupe by name (a
  Recipe that calls `[[foo]]` twice yields `["foo"]`).

  Extracts the full path form (`music_theory/scale`) verbatim — the
  closure walker decides whether to resolve as `music_theory/scale.md`
  or `scale.md`.
  """
  out: list[str] = []
  seen: set[str] = set()
  for match in _WIKILINK_RE.finditer(source):
    namespace, target = match.group(1), match.group(2)
    # Round-trip the namespace into the returned string so the closure
    # walker sees what the author wrote. Dropping it here would make
    # `[[a:x]]` and `[[b:x]]` indistinguishable — which is the exact
    # ambiguity the syntax exists to resolve.
    name = f"{namespace}:{target}" if namespace else target
    if name not in seen:
      seen.add(name)
      out.append(name)
  return out


def _leaf_basename(path_like: str) -> str:
  """`music_theory/scale` → `scale`. `scale` → `scale`.

  Matches the transpiler's `_render_chip_name` behavior — vault-note
  wrappers on the transpile side are keyed by the leaf basename.
  """
  return path_like.rsplit("/", 1)[-1]


def build_vault_note_closure(
  source: str,
  vault_fs: "VaultFS",
  *,
  max_depth: int = 32,
) -> list[dict]:
  """Walk wikilink closure from `source`; return vault-note payload list.

  Each entry: `{name: leaf_basename, recipe_source: str, inputs: list[str]}`.
  Ready to POST as `/run` request body's `vault_notes` field.

  Algorithm:
    - Depth-first walk starting from `source`.
    - For each wikilink, resolve as vault path (try full path first,
      fall back to leaf-basename in vault root) via `VaultFS.
      read_note_content`. Missing / non-action notes are skipped.
    - Deduplicate by note_id (path form) — a shared dependency is
      packaged once.
    - Cycle detection via a per-descent-path set. Cycles raise
      `CircularVaultNoteError`.
    - `max_depth` guards against pathologically deep chains — raises
      `CircularVaultNoteError` if exceeded (rare in practice; sane
      vaults won't approach 32 levels).

  Returns entries in topological-ish order: leaves before roots when
  possible (a note is emitted after its dependencies are walked). The
  transpile-side splice doesn't care about order (Python def is
  hoisted at module scope), but callers may find the ordering useful
  for debugging.
  """
  # Import inside the function to avoid a circular import — vault_fs
  # doesn't need this module.
  from .vault_fs import NoteIdInvalid, NoteNotFound, VaultFSError

  packaged: dict[str, dict] = {}  # note_id (path) → entry
  order: list[str] = []

  def _visit(name: str, on_path: set[str], depth: int) -> None:
    if depth > max_depth:
      raise CircularVaultNoteError(
        f"vault-note closure exceeded max_depth={max_depth} "
        f"(descent path: {' → '.join(on_path)})"
      )

    # Try full path first, then leaf-basename as fallback.
    candidates = [name]
    leaf = _leaf_basename(name)
    if leaf != name:
      candidates.append(leaf)

    for note_id in candidates:
      if note_id in packaged:
        # Already walked. Don't re-emit but also don't recurse — its
        # dependencies were already handled at first-visit time.
        return
      if note_id in on_path:
        cycle = " → ".join(list(on_path) + [note_id])
        raise CircularVaultNoteError(
          f"vault-note cycle detected: {cycle}"
        )
      try:
        content = vault_fs.read_note_content(note_id)
      except (NoteIdInvalid, NoteNotFound, VaultFSError):
        continue  # Try next candidate; else falls through to skip.

      # Only action notes have Recipes. Vanilla / data notes are
      # skipped — no wrapper to emit.
      note_type = content.get("type", "vanilla")
      if note_type != "action":
        return
      recipe = content.get("recipe")
      if recipe is None:
        # Action note with no Recipe body — treat as no-op vault note
        # (emit an empty entry so `[[name]]` resolves rather than
        # NameError).
        recipe = ""

      # Read frontmatter inputs (drain 2026-07-24-1730 exposes them
      # as `content["inputs"]` — the parsed list).
      inputs = content.get("inputs") or []

      # Recurse first (so downstream deps are packaged before this
      # note in `order`).
      new_on_path = on_path | {note_id}
      for referenced in extract_wikilinks(recipe):
        _visit(referenced, new_on_path, depth + 1)

      # Emit under the transpile-side leaf-basename key.
      wrapper_name = _leaf_basename(note_id)
      packaged[note_id] = {
        "name": wrapper_name,
        "recipe_source": recipe,
        "inputs": list(inputs),
      }
      order.append(note_id)
      return

  # Top-level: walk wikilinks in the user's source.
  for name in extract_wikilinks(source):
    _visit(name, set(), 0)

  return [packaged[note_id] for note_id in order]
