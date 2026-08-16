"""Tests for `forge_commit_recipe`'s new `inputs` param.

CW-forge-mcp-commit-recipe-accept-inputs-param (drain 2026-07-27-2005).

Wizard-authored interactive-exercise notes reference their input names
ONLY inside `{{ }}` slot bodies (invisible to auto-derivation). Pre-
drain, MCP had no way to author frontmatter `inputs:` on an action
note — `forge_create_note` hardcoded `inputs: []` and `forge_commit_
recipe` didn't touch inputs. This drain extends `commit_recipe` with
an optional `inputs=` param.

Tests:
  - fresh-note create + commit with inputs stamps frontmatter.
  - existing-note commit with inputs overwrites frontmatter.
  - commit without inputs (back-compat) leaves frontmatter untouched.
  - malformed inputs param → clean error.
  - end-to-end wizard-style workflow.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import commit_recipe, create_note, read_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"music": VaultFS(root=vault)})


@pytest.mark.asyncio
async def test_commit_recipe_with_inputs_stamps_frontmatter_fresh_note(
  single_vault_registry: VaultRegistry,
):
  """Fresh note (path doesn't exist) + inputs=[...] → frontmatter has
  `inputs: [name1, name2]` on disk."""
  result = await commit_recipe.run(
    arguments={
      "note_id": "exercises/name_this_scale",
      "source": "Return {{ guess }}.",
      "inputs": ["guess"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  # commit_recipe may fail its internal run step (which needs live
  # forge-transpile), but the write itself must succeed. So check on-
  # disk state directly, not the tool result's isError.
  vault_fs = single_vault_registry.get("music")
  content = (vault_fs.root / "exercises" / "name_this_scale.md").read_text()
  assert "inputs: [guess]" in content


@pytest.mark.asyncio
async def test_commit_recipe_with_inputs_overwrites_frontmatter_existing_note(
  single_vault_registry: VaultRegistry,
):
  """Existing note has inputs: [] (from create_note_shell) → commit
  with inputs=[guess] overwrites to inputs: [guess]."""
  # Create note first via forge_create_note (writes `inputs: []`).
  await create_note.run(
    arguments={"note_id": "exercises/guess", "description": "Guess it."},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "exercises" / "guess.md"
  assert "inputs: []" in path.read_text()

  await commit_recipe.run(
    arguments={
      "note_id": "exercises/guess",
      "source": "Return {{ guess }}.",
      "expected_version": 0,
      "inputs": ["guess"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  content = path.read_text()
  assert "inputs: [guess]" in content
  # Old value gone.
  assert "inputs: []" not in content


@pytest.mark.asyncio
async def test_commit_recipe_without_inputs_leaves_frontmatter_untouched(
  single_vault_registry: VaultRegistry,
):
  """Back-compat: no `inputs` arg → existing frontmatter inputs
  survives the commit. Pre-drain callers see zero change."""
  # Hand-write a note with `inputs: [tonic, mode]`.
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "seed.md"
  path.write_text(
    "---\n"
    "type: action\n"
    "inputs: [tonic, mode]\n"
    "recipe_version: 0\n"
    "---\n\n"
    "# Description\n\nSeed.\n"
  )
  # Commit a Recipe WITHOUT inputs arg.
  await commit_recipe.run(
    arguments={
      "note_id": "seed",
      "source": 'Return "hello".',
      "expected_version": 0,
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  content = path.read_text()
  # Wizard-authored inputs survived.
  assert "inputs: [tonic, mode]" in content
  # Recipe version bumped as expected.
  assert "recipe_version: 1" in content


@pytest.mark.asyncio
async def test_commit_recipe_empty_inputs_writes_empty_list(
  single_vault_registry: VaultRegistry,
):
  """`inputs=[]` means "clear to inputs: []" — distinct from None
  ("leave alone")."""
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "clear.md"
  path.write_text(
    "---\n"
    "type: action\n"
    "inputs: [old_field]\n"
    "recipe_version: 0\n"
    "---\n\n"
    "# Description\n\nx.\n"
  )
  await commit_recipe.run(
    arguments={
      "note_id": "clear",
      "source": 'Return 1.',
      "expected_version": 0,
      "inputs": [],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  content = path.read_text()
  assert "inputs: []" in content
  assert "old_field" not in content


@pytest.mark.asyncio
async def test_commit_recipe_rejects_malformed_inputs(
  single_vault_registry: VaultRegistry,
):
  """`inputs` must be list-of-string. Non-list or list-of-non-string
  → clean isError, no fs touch."""
  result = await commit_recipe.run(
    arguments={
      "note_id": "malformed",
      "source": "Return 1.",
      "inputs": "not-a-list",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "list of strings" in result["content"][0]["text"].lower()
  # File was not created.
  vault_fs = single_vault_registry.get("music")
  assert not (vault_fs.root / "malformed.md").exists()


@pytest.mark.asyncio
async def test_commit_recipe_rejects_inputs_list_with_non_strings(
  single_vault_registry: VaultRegistry,
):
  """Mixed-type list → clean isError."""
  result = await commit_recipe.run(
    arguments={
      "note_id": "mixed",
      "source": "Return 1.",
      "inputs": ["ok_name", 42],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "list of strings" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_commit_recipe_multiple_inputs_render_inline(
  single_vault_registry: VaultRegistry,
):
  """`inputs=["tonic","mode","depth"]` → `inputs: [tonic, mode, depth]`
  (inline YAML, comma-separated, bare identifiers)."""
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "multi.md"
  path.write_text(
    "---\nrecipe_version: 0\n---\n\n# Description\n\nx.\n"
  )
  await commit_recipe.run(
    arguments={
      "note_id": "multi",
      "source": "Return 1.",
      "expected_version": 0,
      "inputs": ["tonic", "mode", "depth"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert "inputs: [tonic, mode, depth]" in path.read_text()


@pytest.mark.asyncio
async def test_end_to_end_wizard_workflow_readback(
  single_vault_registry: VaultRegistry,
):
  """Wizard-style: commit with inputs, then read_note returns them."""
  await commit_recipe.run(
    arguments={
      "note_id": "exercises/name_this_scale",
      "source": "Return {{ guess }}.",
      "inputs": ["guess"],
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await read_note.run(
    arguments={"note_id": "exercises/name_this_scale"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  inputs = result["structuredContent"]["note"]["inputs"]
  assert inputs == ["guess"]


# ---------------------------------------------------------------------
# Block-form `inputs:` — drain 2026-08-16-2400
# ---------------------------------------------------------------------
#
# Wizard's commit on forge_tutorial/03-functions/mood.md (git_sha
# 6ec25f2) produced frontmatter that is not valid YAML:
#
#     inputs: [style]
#       - style
#
# The rewrite replaced the `inputs:` KEY LINE and left the block
# sequence's continuation line behind, keyless. The plugin then cannot
# parse `type: action`, so the note loses its Run button entirely — a
# successful-looking commit that leaves the note un-runnable.
#
# PyYAML is not a forge-mcp dependency and this is not the drain to make
# it one, so the validity check below is written out: in a frontmatter
# mapping every line is either a `key:` line or an indented continuation
# of a key whose own value was EMPTY. An indented line under a key that
# already carries a value is precisely the corruption.

from forge_mcp.vault_fs import splice_recipe  # noqa: E402


def _frontmatter(raw: str) -> str:
  assert raw.startswith("---\n")
  return raw[4:raw.index("\n---", 4)]


def _parse_frontmatter_mapping(fm_text: str) -> dict[str, object]:
  """Parse a frontmatter block, refusing anything YAML would refuse."""
  result: dict[str, object] = {}
  last_key: str | None = None
  last_key_had_value = True
  for lineno, line in enumerate(fm_text.split("\n"), start=1):
    if not line.strip():
      continue
    if line[0] in " \t":
      if last_key is None:
        raise AssertionError(f"line {lineno}: indented line with no key above: {line!r}")
      if last_key_had_value:
        raise AssertionError(
          f"line {lineno}: {line!r} continues {last_key!r}, which already has "
          f"a value — keyless continuation, not valid YAML"
        )
      item = line.strip()
      if not item.startswith("- "):
        raise AssertionError(f"line {lineno}: unsupported continuation {line!r}")
      result.setdefault(last_key, []).append(item[2:].strip())  # type: ignore[union-attr]
      continue
    if ":" not in line:
      raise AssertionError(f"line {lineno}: not a mapping entry: {line!r}")
    key, _, value = line.partition(":")
    last_key, value = key.strip(), value.strip()
    last_key_had_value = bool(value)
    if value.startswith("[") and value.endswith("]"):
      inner = value[1:-1].strip()
      result[last_key] = [v.strip() for v in inner.split(",")] if inner else []
    elif value:
      result[last_key] = value
    else:
      result[last_key] = []
  return result


_BLOCK_FORM_NOTE = """---
type: action
inputs:
  - style
source_facet: description
sync_state: stale-python
recipe_version: 1
---

# Description

Pick a style.

# Recipe

Input style: str = "cheerful".
Return style.
"""


def test_the_validity_checker_actually_rejects_wizards_corruption():
  """Non-vacuity for the checker itself: feed it the exact bytes wizard
  found on disk and confirm it objects."""
  with pytest.raises(AssertionError, match="keyless continuation"):
    _parse_frontmatter_mapping(
      "type: action\ninputs: [style]\n  - style\nsource_facet: description"
    )


def test_block_form_inputs_rewrite_leaves_valid_yaml():
  """Wizard's exact repro. The dangling `  - style` is the bug."""
  out = splice_recipe(
    _BLOCK_FORM_NOTE, 'Input style: str = "formal".\nReturn style.\n', 2,
    inputs=["style"],
  )
  fm_text = _frontmatter(out)
  fm = _parse_frontmatter_mapping(fm_text)

  assert fm["inputs"] == ["style"]
  assert fm["type"] == "action", (
    "the plugin's run-button gate reads `type`; unparseable frontmatter "
    "hides it and the button disappears"
  )
  assert "\n  - style" not in fm_text, "block continuation left behind"


def test_block_form_multi_item_converts_without_residue():
  note = _BLOCK_FORM_NOTE.replace(
    "inputs:\n  - style\n", "inputs:\n  - bars\n  - velocity\n"
  )
  out = splice_recipe(note, "Return 1.\n", 2, inputs=["bars", "velocity"])
  fm_text = _frontmatter(out)
  fm = _parse_frontmatter_mapping(fm_text)

  assert fm["inputs"] == ["bars", "velocity"]
  assert "  - bars" not in fm_text and "  - velocity" not in fm_text
  # The keys that followed the block must survive, in place.
  assert fm["source_facet"] == "description"
  assert fm["sync_state"] == "stale-python"


def test_flow_form_rewrite_is_unchanged():
  """Regression guard: the path that was already working."""
  note = _BLOCK_FORM_NOTE.replace("inputs:\n  - style\n", "inputs: [style]\n")
  out = splice_recipe(note, "Return 1.\n", 2, inputs=["style", "mood"])
  fm = _parse_frontmatter_mapping(_frontmatter(out))

  assert fm["inputs"] == ["style", "mood"]
  assert fm["type"] == "action"


def test_note_without_any_inputs_key_gains_one_cleanly():
  note = _BLOCK_FORM_NOTE.replace("inputs:\n  - style\n", "")
  out = splice_recipe(note, "Return 1.\n", 2, inputs=["style"])
  fm = _parse_frontmatter_mapping(_frontmatter(out))

  assert fm["inputs"] == ["style"]
  assert fm["type"] == "action"


def test_a_key_after_the_block_is_not_swallowed():
  """The fix must consume the block's OWN continuation lines and stop —
  not eat whatever follows."""
  out = splice_recipe(_BLOCK_FORM_NOTE, "Return 1.\n", 2, inputs=["style"])
  fm = _parse_frontmatter_mapping(_frontmatter(out))

  assert set(fm) == {
    "type", "inputs", "source_facet", "sync_state", "recipe_version",
  }
  assert fm["recipe_version"] == "2"


def test_inputs_none_leaves_a_block_form_note_untouched():
  """Back-compat: callers that pass no inputs must not trigger any of
  this. The block form stays exactly as authored."""
  out = splice_recipe(_BLOCK_FORM_NOTE, "Return 1.\n", 2)
  fm_text = _frontmatter(out)

  assert "inputs:\n  - style" in fm_text
  _parse_frontmatter_mapping(fm_text)
