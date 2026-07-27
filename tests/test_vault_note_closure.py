"""Tests for the vault-note wikilink extractor + closure walker.

CW-forge-run-recipe-vault-note-invocation-arch-b-pivot
(drain 2026-07-27-1400).

Covers:
  - extract_wikilinks: parses parser-grammar-compliant wikilink shapes.
  - build_vault_note_closure: happy paths (leaf-only, transitive,
    engine chip pass-through), edge cases (missing note, vanilla
    note skip, cycle detection).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_note_closure import (
  CircularVaultNoteError,
  build_vault_note_closure,
  extract_wikilinks,
)


@pytest.fixture
def vault(tmp_path: Path) -> VaultFS:
  root = tmp_path / "vault"
  root.mkdir()
  return VaultFS(root=root)


def _action_note(vault: VaultFS, note_id: str, recipe: str, inputs=None) -> None:
  """Write an action note with a Recipe facet."""
  path = vault.note_path(note_id)
  path.parent.mkdir(parents=True, exist_ok=True)
  inputs_line = ""
  if inputs:
    inputs_line = f"inputs: [{', '.join(inputs)}]\n"
  path.write_text(
    "---\n"
    "type: action\n"
    f"{inputs_line}"
    "recipe_version: 1\n"
    "---\n\n"
    "# Description\n\ndoc.\n\n"
    "# Recipe\n\n"
    f"{recipe}\n"
  )


# ---------------------------------------------------------------------------
# extract_wikilinks — grammar coverage
# ---------------------------------------------------------------------------


def test_extract_wikilinks_bare_names():
  """`[[foo]]` and `[[bar]]` extracted, order preserved, deduped."""
  src = "Let x = Call [[foo]].\nReturn Call [[bar]] with y=Call [[foo]]."
  assert extract_wikilinks(src) == ["foo", "bar"]


def test_extract_wikilinks_path_shape():
  """`[[music_theory/scale]]` extracted verbatim (with slash)."""
  src = "Return Call [[music_theory/scale]] with tonic=\"C\"."
  assert extract_wikilinks(src) == ["music_theory/scale"]


def test_extract_wikilinks_hyphen_and_underscore():
  """Hyphen + underscore + alphanumeric all valid per parser grammar."""
  src = "Call [[my_snippet-42]]."
  assert extract_wikilinks(src) == ["my_snippet-42"]


def test_extract_wikilinks_none_in_source():
  """No wikilinks → empty list."""
  assert extract_wikilinks("Return 42.") == []


def test_extract_wikilinks_rejects_invalid_leading_char():
  """Leading digit rules out list-literal look-alikes (parser
  invariant)."""
  src = "Return [[0,1,2]]."  # Not a valid wikilink shape.
  assert extract_wikilinks(src) == []


# ---------------------------------------------------------------------------
# build_vault_note_closure — happy paths
# ---------------------------------------------------------------------------


def test_closure_single_referenced_note(vault: VaultFS):
  """`[[greeting]]` → one vault-note entry with its recipe body."""
  _action_note(vault, "greeting", 'Return "hello".')
  result = build_vault_note_closure("Return Call [[greeting]].", vault)
  assert len(result) == 1
  assert result[0]["name"] == "greeting"
  assert 'Return "hello".' in result[0]["recipe_source"]
  assert result[0]["inputs"] == []


def test_closure_transitive_dependency(vault: VaultFS):
  """A calls B: both packaged, B before A in `order`."""
  _action_note(vault, "outer", "Return Call [[inner]].")
  _action_note(vault, "inner", 'Return 42.')
  result = build_vault_note_closure("Return Call [[outer]].", vault)
  assert len(result) == 2
  # Inner emitted first (leaves before roots).
  assert result[0]["name"] == "inner"
  assert result[1]["name"] == "outer"


def test_closure_engine_chip_passes_through(vault: VaultFS):
  """`[[nth]]` (engine chip, not in vault) is silently skipped —
  runtime resolves via engine imports."""
  result = build_vault_note_closure(
    "Return Call [[nth]] with lst=[10,20,30], index=1.", vault,
  )
  assert result == []


def test_closure_mixed_engine_and_vault(vault: VaultFS):
  """Vault-note is packaged; engine chip is passed through."""
  _action_note(vault, "my_helper", "Return Call [[nth]] with lst=[1,2,3], index=0.")
  result = build_vault_note_closure(
    "Return Call [[my_helper]].", vault,
  )
  assert len(result) == 1
  assert result[0]["name"] == "my_helper"


def test_closure_path_shape_resolves_leaf(vault: VaultFS):
  """`[[music_theory/scale]]` resolves to `music_theory/scale.md` and
  emits with leaf basename `scale` (matches transpiler's chip-name
  rendering)."""
  _action_note(vault, "music_theory/scale", 'Return "C major".')
  result = build_vault_note_closure(
    "Return Call [[music_theory/scale]].", vault,
  )
  assert len(result) == 1
  # Leaf basename — the transpile side wraps by leaf name.
  assert result[0]["name"] == "scale"


def test_closure_vanilla_note_skipped(vault: VaultFS):
  """A vanilla note (no `type: action` frontmatter) is skipped even if
  the wikilink resolves to it — treated as an engine-chip fallback."""
  # Write a vanilla note.
  (vault.root / "prose.md").write_text("# Prose\n\nJust text.\n")
  result = build_vault_note_closure("Return Call [[prose]].", vault)
  assert result == []


def test_closure_missing_note_skipped(vault: VaultFS):
  """Wikilink whose target doesn't exist is silently skipped."""
  result = build_vault_note_closure("Return Call [[nonexistent]].", vault)
  assert result == []


def test_closure_inputs_threaded_through(vault: VaultFS):
  """Frontmatter `inputs: [x, y]` surfaces in the closure entry."""
  _action_note(vault, "greet", "Return name.", inputs=["name"])
  result = build_vault_note_closure(
    'Return Call [[greet]] with name="world".', vault,
  )
  assert len(result) == 1
  assert result[0]["inputs"] == ["name"]


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_closure_direct_cycle_raises(vault: VaultFS):
  """A vault-note that calls itself raises CircularVaultNoteError."""
  _action_note(vault, "self_ref", "Return Call [[self_ref]].")
  with pytest.raises(CircularVaultNoteError) as excinfo:
    build_vault_note_closure("Return Call [[self_ref]].", vault)
  assert "self_ref" in str(excinfo.value)


def test_closure_indirect_cycle_raises(vault: VaultFS):
  """A → B → A raises CircularVaultNoteError naming the chain."""
  _action_note(vault, "a", "Return Call [[b]].")
  _action_note(vault, "b", "Return Call [[a]].")
  with pytest.raises(CircularVaultNoteError) as excinfo:
    build_vault_note_closure("Return Call [[a]].", vault)
  msg = str(excinfo.value)
  assert "a" in msg
  assert "b" in msg


def test_closure_diamond_ok(vault: VaultFS):
  """A → B, A → C, B → D, C → D: D only appears once."""
  _action_note(vault, "a", "Return Call [[b]] with x=Call [[c]].")
  _action_note(vault, "b", "Return Call [[d]].")
  _action_note(vault, "c", "Return Call [[d]].")
  _action_note(vault, "d", "Return 1.")
  result = build_vault_note_closure("Return Call [[a]].", vault)
  names = [e["name"] for e in result]
  assert names.count("d") == 1
  assert set(names) == {"a", "b", "c", "d"}
