"""Cross-vault wikilink resolution. Drain 2026-08-05-1430 (Phase 3a).

The resolution order and collision policy from
`forge/docs/specs/vault-imports.md`, exercised against an in-memory
vault map. No filesystem, no registry, no network — `resolve_link`
takes an `exists` predicate precisely so every ordering and ambiguity
case is cheap to state and cheap to run.

The collision cases carry the weight. Local-shadows-import and
ambiguous-across-imports are the two decisions that make cross-vault
composition safe: the first stops somebody else's repo from changing
what your note means, the second stops a coin-flip from deciding it.
"""
from __future__ import annotations

import pytest

from forge_mcp.vault_link_resolver import (
  LinkRef,
  Resolved,
  Unresolved,
  describe_search,
  resolve_link,
  split_namespace,
)

# vault key -> set of note ids it holds. `None` is the local vault.
Vaults = dict[str | None, set[str]]


def make_exists(vaults: Vaults):
  def exists(source_vault: str | None, note_id: str) -> bool:
    return note_id in vaults.get(source_vault, set())
  return exists


def resolve(wire: str, vaults: Vaults, imports: list[str]):
  """Order of `imports` IS declaration order — that is what's tested."""
  return resolve_link(
    wire,
    local_vault="music-theory",
    imports={name: object() for name in imports},
    exists=make_exists(vaults),
  )


# ------------------------------------------------------------ split

class TestSplitNamespace:
  def test_bare(self):
    assert split_namespace("scale") == LinkRef("scale", "scale", None)

  def test_namespaced(self):
    assert split_namespace("music-core:scale") == LinkRef(
      "music-core:scale", "scale", "music-core"
    )

  def test_path_shaped_id_is_not_a_namespace(self):
    # `music_theory/scales/scale` has no colon — the whole thing is the
    # id. Slashes are path structure, not namespacing.
    got = split_namespace("music_theory/scales/scale")
    assert got.namespace is None
    assert got.note_id == "music_theory/scales/scale"

  def test_splits_on_the_first_colon_only(self):
    # A namespaced link into a path-shaped id. Splitting on the last
    # colon, or on every colon, would mangle this.
    got = split_namespace("music-core:scales/major")
    assert got.namespace == "music-core"
    assert got.note_id == "scales/major"

  @pytest.mark.parametrize("degenerate", [":scale", "music-core:", ":"])
  def test_degenerate_colons_are_not_namespaces(self, degenerate):
    # Half a namespace is not a namespace. Treated as a bare (probably
    # broken) id so the not-found path reports it verbatim rather than
    # inventing an empty vault name.
    assert split_namespace(degenerate).namespace is None

  def test_raw_is_preserved_for_error_text(self):
    assert split_namespace("music-core:scale").raw == "music-core:scale"


# ------------------------------------------- bare form, happy paths

class TestBareResolution:
  def test_local_wins_over_import(self):
    """The load-bearing rule: an import can never shadow a local note.

    A vault's own notes are the ones its author controls. If an import
    could win, a vault's behaviour would change when somebody else
    pushed to a repo it merely references.
    """
    got = resolve(
      "scale",
      {None: {"scale"}, "music-core": {"scale"}},
      ["music-core"],
    )
    assert isinstance(got, Resolved)
    assert got.source_vault is None

  def test_local_wins_even_against_several_imports(self):
    # Local-first is NOT subject to the ambiguity rule — two imports
    # both holding it is irrelevant when local has it.
    got = resolve(
      "scale",
      {None: {"scale"}, "a": {"scale"}, "b": {"scale"}},
      ["a", "b"],
    )
    assert isinstance(got, Resolved)
    assert got.source_vault is None

  def test_import_used_when_local_absent(self):
    got = resolve("scale", {"music-core": {"scale"}}, ["music-core"])
    assert isinstance(got, Resolved)
    assert got.source_vault == "music-core"

  def test_declaration_order_decides_when_only_one_import_has_it(self):
    # Not a collision: only `b` holds it, so order is irrelevant here.
    # Pinned to prove the walk visits every import, not just the first.
    got = resolve("scale", {"b": {"scale"}}, ["a", "b"])
    assert isinstance(got, Resolved)
    assert got.source_vault == "b"

  def test_engine_fallback_when_nothing_in_the_vault_graph(self):
    got = resolve("print", {}, ["music-core"])
    assert isinstance(got, Resolved)
    assert got.is_engine_fallback is True
    assert got.source_vault is None


# ------------------------------------------------ bare form, collision

class TestAmbiguity:
  def test_two_imports_is_an_error_not_a_pick(self):
    got = resolve("scale", {"a": {"scale"}, "b": {"scale"}}, ["a", "b"])
    assert isinstance(got, Unresolved)
    assert got.reason == "ambiguous"
    assert got.candidates == ("a", "b")

  def test_error_names_both_vaults_and_suggests_both_fixes(self):
    got = resolve("scale", {"a": {"scale"}, "b": {"scale"}}, ["a", "b"])
    msg = got.message
    assert "a" in msg and "b" in msg
    # The spec requires a disambiguation suggestion, and it must be
    # copy-pasteable — the author should not have to work out the
    # syntax from prose.
    assert "[[a:scale]]" in msg
    assert "[[b:scale]]" in msg

  def test_three_way_ambiguity_lists_all_three(self):
    got = resolve(
      "scale", {"a": {"scale"}, "b": {"scale"}, "c": {"scale"}}, ["a", "b", "c"]
    )
    assert got.candidates == ("a", "b", "c")

  def test_candidates_follow_declaration_order(self):
    # Same two vaults, reversed declaration. The report should follow
    # the manifest, not the alphabet — that's what the author reads.
    got = resolve("scale", {"a": {"scale"}, "b": {"scale"}}, ["b", "a"])
    assert got.candidates == ("b", "a")


# ------------------------------------------------------ namespaced form

class TestNamespacedResolution:
  def test_direct_hit(self):
    got = resolve(
      "music-core:scale", {"music-core": {"scale"}}, ["music-core"]
    )
    assert isinstance(got, Resolved)
    assert got.source_vault == "music-core"

  def test_disambiguates_what_would_otherwise_be_ambiguous(self):
    got = resolve("b:scale", {"a": {"scale"}, "b": {"scale"}}, ["a", "b"])
    assert isinstance(got, Resolved)
    assert got.source_vault == "b"

  def test_beats_local_because_the_author_said_so(self):
    # The one case where local does NOT win. `[[music-core:scale]]` is
    # an explicit instruction; honouring local instead would silently
    # contradict it.
    got = resolve(
      "music-core:scale",
      {None: {"scale"}, "music-core": {"scale"}},
      ["music-core"],
    )
    assert isinstance(got, Resolved)
    assert got.source_vault == "music-core"

  def test_missing_target_does_NOT_fall_back(self):
    """No fallback is the whole point of the syntax.

    `scale` exists locally and in another import. A fallback would
    resolve happily and the author would never learn that music-core
    does not have what they thought it had.
    """
    got = resolve(
      "music-core:scale",
      {None: {"scale"}, "other": {"scale"}},
      ["music-core", "other"],
    )
    assert isinstance(got, Unresolved)
    assert got.reason == "not-found-in-namespace"
    assert "does not fall back" in got.message

  def test_unknown_namespace_names_what_is_declared(self):
    got = resolve("bogus:scale", {"music-core": {"scale"}}, ["music-core"])
    assert isinstance(got, Unresolved)
    assert got.reason == "unknown-namespace"
    assert "bogus" in got.message
    assert "music-core" in got.message  # what they could have written

  def test_unknown_namespace_with_no_imports_says_none(self):
    got = resolve("bogus:scale", {}, [])
    assert isinstance(got, Unresolved)
    assert "none" in got.message

  def test_local_namespace_resolves_locally(self):
    got = resolve("local:scale", {None: {"scale"}}, ["music-core"])
    assert isinstance(got, Resolved)
    assert got.source_vault is None

  def test_local_namespace_does_not_fall_back_to_imports(self):
    # Symmetric with the import case: `[[local:x]]` means local, and
    # silently resolving to an import would be the same lie.
    got = resolve("local:scale", {"music-core": {"scale"}}, ["music-core"])
    assert isinstance(got, Unresolved)
    assert got.reason == "not-found-in-namespace"


# -------------------------------------------------------- error text

class TestErrorText:
  def test_describe_search_names_every_location(self):
    text = describe_search(
      split_namespace("scale"),
      local_vault="music-theory",
      imports={"music-core": object(), "extras": object()},
    )
    assert "music-theory" in text
    assert "music-core" in text
    assert "extras" in text
    assert "engine library" in text

  def test_describe_search_suggests_the_namespaced_form(self):
    text = describe_search(
      split_namespace("scale"),
      local_vault="music-theory",
      imports={},
    )
    assert "[[<import-name>:scale]]" in text

  def test_messages_quote_what_the_author_wrote(self):
    # Not the parsed id — the raw link. Someone scanning their Recipe
    # for the failing line needs to match on what is in the file.
    got = resolve("music-core:scale", {}, [])
    assert "music-core:scale" in got.message


# ----------------------------------------------------------- shape

class TestReturnShape:
  def test_failures_are_returned_not_raised(self):
    """Deliberate: a closure walk should report every bad link at once.

    Raising would surface them one failed run at a time — fix a link,
    re-run, discover the next one.
    """
    got = resolve("bogus:x", {}, [])
    assert isinstance(got, Unresolved)

  def test_resolved_is_hashable_and_frozen(self):
    got = resolve("scale", {None: {"scale"}}, [])
    with pytest.raises(Exception):
      got.source_vault = "mutated"  # type: ignore[misc]

  def test_no_imports_at_all_is_the_common_case_and_works(self):
    # Every vault today has zero imports. The resolver must be a no-op
    # relative to previous behaviour for them.
    assert resolve("scale", {None: {"scale"}}, []).source_vault is None
    assert resolve("print", {}, []).is_engine_fallback is True
