"""Cross-vault wikilink resolution. Drain 2026-08-05-1430 (Phase 3a).

Implements the resolution order and collision policy frozen in
`forge/docs/specs/vault-imports.md`:

  1. local vault
  2. declared imports, in `forge.toml` declaration order
  3. engine library
  4. no match -> error naming everywhere searched

Plus the namespaced form `[[import-name:note-id]]`, which skips the
order entirely and looks only in the named import. There is no fallback
from a namespaced miss — that is the point of writing the namespace.

WHY THIS IS A SEPARATE MODULE
-----------------------------
`build_vault_note_closure` resolves through a single `VaultFS`, which
is bound to one vault root. Cross-vault resolution is a different
question — "which of several roots owns this name, and is that
ambiguous" — and it is answerable without touching a filesystem if the
caller supplies an existence predicate. Keeping it separate means the
whole resolution order and every collision case is unit-testable
against an in-memory map, and the walker just calls it.

ON `WikilinkRef`
----------------
The drain prompt's fix shape is written against a `WikilinkRef` object
with `.namespace` and `.note_id`. **No such class exists.** Drain
0710's `extract_wikilinks` returns `list[str]` and round-trips a
namespace INTO the string (`"music-core:scale"`), deliberately:

    # ADDITIVE. The prefix group is optional, so every existing bare
    # `[[note-id]]` matches exactly as before ... Nothing downstream
    # has to learn about namespaces until it wants to.

Something now wants to. Rather than change that return type — which
would touch every existing caller for no benefit to them — this module
adds `split_namespace`, which turns the wire string back into its two
parts at the one place that cares. The extractor's contract is
unchanged and its callers stay untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping


@dataclass(frozen=True)
class LinkRef:
  """One wikilink, split into its optional namespace and its note id.

  `raw` is what the author actually wrote, kept for error messages —
  telling someone `[[music-core:scale]]` failed is more useful than
  telling them `scale` failed.
  """

  raw: str
  note_id: str
  namespace: str | None = None


@dataclass(frozen=True)
class Resolved:
  """A wikilink that found exactly one home."""

  ref: LinkRef
  # `None` for the local vault; otherwise the import NAME (per spec —
  # the name, never the on-disk path).
  source_vault: str | None
  # True when nothing in local or imports claimed it and the caller
  # should fall through to the engine library.
  is_engine_fallback: bool = False


@dataclass(frozen=True)
class Unresolved:
  """A wikilink that found zero homes, or more than one.

  Returned rather than raised so a caller walking a whole closure can
  collect every problem in one pass instead of surfacing them one
  failed run at a time.
  """

  ref: LinkRef
  reason: str
  message: str
  # Populated on ambiguity: the import names that all claim this id.
  candidates: tuple[str, ...] = field(default_factory=tuple)


# `local` is reserved: `[[local:x]]` means "the containing vault", so an
# import may not take the name. Enforced at parse time by
# `vault_imports.RESERVED_IMPORT_NAMES`; honoured here so the resolver
# is correct even if called with a hand-built mapping.
LOCAL_NAMESPACE = "local"


def split_namespace(wire: str) -> LinkRef:
  """`"music-core:scale"` -> `LinkRef(raw, "scale", "music-core")`.

  A bare name yields `namespace=None`. Splits on the FIRST colon only:
  a note id may itself contain a path (`music_theory/scales/scale`),
  and only the leading segment can be a namespace.
  """
  if ":" in wire:
    head, _, tail = wire.partition(":")
    if head and tail:
      return LinkRef(raw=wire, note_id=tail, namespace=head)
  return LinkRef(raw=wire, note_id=wire, namespace=None)


def resolve_link(
  wire: str,
  *,
  local_vault: str,
  imports: Mapping[str, object],
  exists: Callable[[str | None, str], bool],
) -> Resolved | Unresolved:
  """Resolve one wikilink against the local vault and its imports.

  `imports` is an ordered mapping of import-name -> anything; only the
  keys and their ORDER matter here. Python dicts preserve insertion
  order, and `parse_imports` builds its result by iterating the TOML
  table, so declaration order survives into this call.

  `exists(source_vault, note_id)` answers "does this vault hold this
  note", with `None` meaning the local vault. Injected rather than
  hardcoded to a filesystem so the order and collision rules can be
  tested exhaustively against a dict.
  """
  ref = split_namespace(wire)

  # --- namespaced form: exact, no fallback -------------------------
  if ref.namespace is not None:
    if ref.namespace == LOCAL_NAMESPACE:
      if exists(None, ref.note_id):
        return Resolved(ref=ref, source_vault=None)
      return Unresolved(
        ref=ref,
        reason="not-found-in-namespace",
        message=(
          f"[[{ref.raw}]] names the local vault explicitly, but "
          f"'{ref.note_id}' is not in {local_vault}."
        ),
      )
    if ref.namespace not in imports:
      declared = ", ".join(imports) or "none"
      return Unresolved(
        ref=ref,
        reason="unknown-namespace",
        message=(
          f"[[{ref.raw}]] names import '{ref.namespace}', which "
          f"{local_vault} does not declare. Declared imports: "
          f"{declared}."
        ),
      )
    if exists(ref.namespace, ref.note_id):
      return Resolved(ref=ref, source_vault=ref.namespace)
    # Deliberately no fallback to local or to other imports. The
    # author named a vault; resolving somewhere else would silently
    # contradict what they wrote.
    return Unresolved(
      ref=ref,
      reason="not-found-in-namespace",
      message=(
        f"[[{ref.raw}]] not found in import '{ref.namespace}'. "
        "A namespaced link does not fall back to other vaults — "
        f"drop the prefix to search all of them."
      ),
    )

  # --- slash form: `[[import-name/path/to/note]]` -------------------
  # Drain 2026-08-13-0430. A slash link carries its whole string as
  # note_id, so `exists("music-core", "music-core/percussion_lab/x")`
  # hunts that FULL path inside music-core and misses — the link fell
  # through to engine-fallback and died later as a NameError. When the
  # first segment names a declared import, retry the remainder against
  # that vault.
  #
  # Note the deliberate asymmetry with the bare form below: there,
  # local-first wins outright and is exempt from the collision rule.
  # Here a local/import tie is an ERROR, per this drain's section 4.3.
  # The reasoning differs because the shapes differ — a bare `[[x]]`
  # naming a local note is unambiguous authorial intent, whereas
  # `[[music-core/...]]` reads as naming a vault, so a local directory
  # that happens to share the name is a genuine collision the author
  # should be told about rather than have silently resolved.
  slash_import: str | None = None
  slash_remainder: str | None = None
  if "/" in ref.note_id:
    head, _, rest = ref.note_id.partition("/")
    if head in imports and rest and exists(head, rest):
      slash_import, slash_remainder = head, rest

  if slash_import is not None and exists(None, ref.note_id):
    return Unresolved(
      ref=ref,
      reason="ambiguous",
      message=(
        f"[[{ref.raw}]] is ambiguous — it resolves both as a local path "
        f"in {local_vault} and as '{slash_remainder}' in import "
        f"'{slash_import}'. Rename the local path, or drop the "
        f"'{slash_import}/' prefix if you meant the local one."
      ),
      candidates=(local_vault, slash_import),
    )

  # --- bare form: local first --------------------------------------
  # Local-first is deliberate and is NOT subject to the collision rule
  # below: a vault's own notes are the ones its author controls, and an
  # import shadowing one would make a vault's behaviour depend on
  # somebody else's repo.
  if exists(None, ref.note_id):
    return Resolved(ref=ref, source_vault=None)

  if slash_import is not None:
    # Strip the vault-name segment so downstream lookups search the
    # importing vault's own tree, not a path prefixed with its name.
    return Resolved(
      ref=LinkRef(raw=ref.raw, note_id=slash_remainder, namespace=ref.namespace),
      source_vault=slash_import,
    )

  hits = [name for name in imports if exists(name, ref.note_id)]

  if len(hits) > 1:
    listed = ", ".join(hits)
    suggestion = " or ".join(f"[[{name}:{ref.note_id}]]" for name in hits)
    return Unresolved(
      ref=ref,
      reason="ambiguous",
      message=(
        f"[[{ref.raw}]] is ambiguous — {listed} all define it. "
        f"Disambiguate with {suggestion}."
      ),
      candidates=tuple(hits),
    )

  if hits:
    return Resolved(ref=ref, source_vault=hits[0])

  # Nothing in the vault graph. The engine library is the last stop;
  # the caller owns that lookup because only it knows the active
  # domains.
  return Resolved(ref=ref, source_vault=None, is_engine_fallback=True)


def describe_search(
  ref: LinkRef, *, local_vault: str, imports: Mapping[str, object]
) -> str:
  """Human-readable list of everywhere a bare link was looked for.

  For the compile error the spec asks for: "naming every location
  searched". Kept separate from `resolve_link` because the caller may
  only want it once, after the engine lookup has also missed.
  """
  places = [f"local vault ({local_vault})"]
  places += [f"import '{name}'" for name in imports]
  places.append("engine library")
  return (
    f"[[{ref.raw}]] not found. Searched: {'; '.join(places)}. "
    f"If it lives in an undeclared vault, add it to [imports] in "
    f"forge.toml, then reference it as [[<import-name>:{ref.note_id}]]."
  )
