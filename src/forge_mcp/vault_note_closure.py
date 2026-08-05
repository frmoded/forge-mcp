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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .vault_link_resolver import Resolved, Unresolved, resolve_link

if TYPE_CHECKING:
  from .vault_fs import VaultFS


@dataclass(frozen=True)
class ClosureResolutionError:
  """One wikilink the closure walker could not resolve.

  Drain 2026-08-05-1900 (vault-split 3c). Collected rather than raised
  so one pass over a Recipe reports every broken link; the caller
  decides the surface (run_recipe fails the call, a future palette
  might render inline).

  `origin_note` is the note id whose Recipe contains the link (or the
  caller-supplied entry label for the user's own source); `wikilink` is
  the wire text exactly as the author wrote it, namespace included.
  """

  origin_note: str
  wikilink: str
  reason: str
  message: str


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
  vault_fs: VaultFS,
  *,
  max_depth: int = 32,
  local_vault_name: str = "local",
  imports: Mapping[str, VaultFS] | None = None,
  errors: list[ClosureResolutionError] | None = None,
  origin_label: str = "<entry recipe>",
) -> list[dict]:
  """Walk wikilink closure from `source`; return vault-note payload list.

  Each entry: `{name: leaf_basename, recipe_source: str, inputs: list[str]}`.
  Ready to POST as `/run` request body's `vault_notes` field.

  Drain 2026-08-05-1900 (vault-split 3c): resolution is delegated to
  `vault_link_resolver.resolve_link` with the registry-provided
  `imports` roots ({import_name: VaultFS}). Bare links resolve local
  first, then declared imports in order, then engine fallback (skip —
  the transpile side owns engine lookup). Namespaced links
  (`[[child:note]]`) look ONLY in the named import. Unresolvable links
  (unknown namespace, namespaced miss, ambiguous bare hit across
  imports) are appended to `errors` as `ClosureResolutionError` and the
  walk continues, so one pass reports every problem. With no `imports`
  and no namespaced links, behavior is unchanged from the single-vault
  walker.

  Notes reached through an import are walked IN THAT VAULT's tree:
  their bare links resolve against the imported vault as local. An
  imported vault's own [imports] are not consulted (transitive imports
  are cycle-checked at registration but not walkable in this phase), so
  namespaced links inside an imported note surface as unknown-namespace
  errors.

  Algorithm (unchanged where not stated above):
    - Depth-first; local candidates try full path then leaf-basename.
    - Deduplicate by (vault, note_id) — a shared dependency is
      packaged once.
    - Cycle detection via a per-descent-path set over (vault, note_id).
      Cycles raise `CircularVaultNoteError`.
    - `max_depth` guards pathologically deep chains.

  Returns entries in topological-ish order: leaves before roots when
  possible. The transpile-side splice doesn't care about order (Python
  def is hoisted at module scope), but callers may find the ordering
  useful for debugging.
  """
  # Import inside the function to avoid a circular import — vault_fs
  # doesn't need this module.
  from .vault_fs import NoteIdInvalid, NoteNotFound, VaultFSError

  import_roots: dict[str, VaultFS] = dict(imports or {})
  collected = errors if errors is not None else []

  # (vault_key, note_id) → entry; vault_key None means the local vault.
  packaged: dict[tuple[str | None, str], dict] = {}
  order: list[tuple[str | None, str]] = []

  def _find_note_id(fs: VaultFS, name: str) -> str | None:
    """The concrete note_id `name` resolves to in `fs`, trying the full
    path form first, then the leaf basename — or None."""
    candidates = [name]
    leaf = _leaf_basename(name)
    if leaf != name:
      candidates.append(leaf)
    for note_id in candidates:
      try:
        if fs.note_path(note_id).is_file():
          return note_id
      except (NoteIdInvalid, VaultFSError):
        continue
    return None

  def _fs_for(vault_key: str | None) -> VaultFS:
    return vault_fs if vault_key is None else import_roots[vault_key]

  def _visit_resolved(
    vault_key: str | None,
    note_id: str,
    origin: str,
    on_path: set[tuple[str | None, str]],
    depth: int,
  ) -> None:
    key = (vault_key, note_id)
    if key in packaged:
      # Already walked. Don't re-emit but also don't recurse — its
      # dependencies were already handled at first-visit time.
      return
    if key in on_path:
      cycle = " → ".join(nid for _, nid in list(on_path)) + f" → {note_id}"
      raise CircularVaultNoteError(f"vault-note cycle detected: {cycle}")

    fs = _fs_for(vault_key)
    try:
      content = fs.read_note_content(note_id)
    except (NoteIdInvalid, NoteNotFound, VaultFSError):
      return  # Existence raced away between resolve and read; skip.

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
    # note in `order`). Inside an imported vault, THAT vault is
    # local and no further imports apply (transitive scope note in
    # the function docstring).
    new_on_path = on_path | {key}
    frame_imports = import_roots if vault_key is None else {}
    frame_local_name = local_vault_name if vault_key is None else vault_key
    for wire in extract_wikilinks(recipe):
      _resolve_and_visit(
        wire, vault_key, frame_local_name, frame_imports,
        origin=note_id, on_path=new_on_path, depth=depth + 1,
      )

    # Emit under the transpile-side leaf-basename key.
    packaged[key] = {
      "name": _leaf_basename(note_id),
      "recipe_source": recipe,
      "inputs": list(inputs),
    }
    order.append(key)

  def _resolve_and_visit(
    wire: str,
    frame_vault_key: str | None,
    frame_local_name: str,
    frame_imports: Mapping[str, VaultFS],
    *,
    origin: str,
    on_path: set[tuple[str | None, str]],
    depth: int,
  ) -> None:
    if depth > max_depth:
      raise CircularVaultNoteError(
        f"vault-note closure exceeded max_depth={max_depth} "
        f"(descent path: {' → '.join(nid for _, nid in on_path)})"
      )

    frame_fs = _fs_for(frame_vault_key)

    def exists(source_vault: str | None, note_id: str) -> bool:
      fs = frame_fs if source_vault is None else frame_imports[source_vault]
      return _find_note_id(fs, note_id) is not None

    outcome = resolve_link(
      wire,
      local_vault=frame_local_name,
      imports=frame_imports,
      exists=exists,
    )
    if isinstance(outcome, Unresolved):
      collected.append(ClosureResolutionError(
        origin_note=origin,
        wikilink=wire,
        reason=outcome.reason,
        message=f"{origin}: {outcome.message}",
      ))
      return
    assert isinstance(outcome, Resolved)
    if outcome.is_engine_fallback:
      # Nothing in the vault graph claimed it — assume engine chip;
      # the transpile side owns that lookup (pre-drain-1900 behavior).
      return

    if outcome.source_vault is None:
      target_key = frame_vault_key
      target_fs = frame_fs
    else:
      target_key = outcome.source_vault
      target_fs = frame_imports[outcome.source_vault]
    note_id = _find_note_id(target_fs, outcome.ref.note_id)
    if note_id is None:
      return  # Raced away post-resolve; treat as engine fallback.
    _visit_resolved(target_key, note_id, origin, on_path, depth)

  # Top-level: walk wikilinks in the user's source against the local
  # vault + its registered imports.
  for wire in extract_wikilinks(source):
    _resolve_and_visit(
      wire, None, local_vault_name, import_roots,
      origin=origin_label, on_path=set(), depth=0,
    )

  return [packaged[key] for key in order]
