"""Drain 2026-07-31-1350 — action-note Description editing.

Covers acceptance criteria 2-8. The git-backed case (criterion 6) runs
against a real `git init` repo, same approach as
test_auto_commit_writes.py from drain 1130.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge_mcp.description_facet import (
  DescriptionFacetError,
  rewrite_description_facet,
)
from forge_mcp.tools import edit_markdown_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

ACTION_NOTE = """---
type: action
source_facet: python
sync_state: stale-both
description_hash: abc123
recipe_hash: def456
python_hash: 789abc
---

# Description

Play a [[exercises/complete_this_scale_challenge]] then check it.

# Recipe

Call play_scale with tonic = "C"

# Python

def run():
    return play_scale("C")
"""


def _write(root: Path, rel: str, text: str) -> Path:
  path = root / rel
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")
  return path


@pytest.fixture
def vault(tmp_path: Path) -> tuple[Path, VaultRegistry]:
  root = (tmp_path / "vault").resolve()
  root.mkdir()
  return root, VaultRegistry({"main": VaultFS(root=root)})


async def _run(registry: VaultRegistry, **args) -> dict:
  return await edit_markdown_note.run(args, bearer="tok", vault_registry=registry)


# --- criterion 2: backward compat ------------------------------------


@pytest.mark.asyncio
async def test_vanilla_note_facet_body_replaces_whole_body(vault):
  root, registry = vault
  _write(root, "notes/plain.md", "old content\n")

  result = await _run(registry, note_id="notes/plain", body="new content\n")

  assert result.get("isError") is not True, result
  assert (root / "notes/plain.md").read_text() == "new content\n"


@pytest.mark.asyncio
async def test_vanilla_note_default_facet_is_body(vault):
  """Omitting `facet` must behave exactly as before this drain."""
  root, registry = vault
  _write(root, "notes/plain.md", "old\n")

  # No `facet` key at all — the pre-drain call shape.
  result = await _run(registry, note_id="notes/plain", body="new\n")

  assert result.get("isError") is not True
  assert (root / "notes/plain.md").read_text() == "new\n"


# --- criteria 3 + 5: mismatched combinations -------------------------


@pytest.mark.asyncio
async def test_vanilla_note_rejects_facet_description(vault):
  root, registry = vault
  _write(root, "notes/plain.md", "content\n")

  result = await _run(
    registry, note_id="notes/plain", body="x", facet="description",
  )

  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "vanilla note" in text
  assert "no `# Description` facet" in text
  # Names the shape that WOULD work.
  assert "facet='body'" in text
  assert (root / "notes/plain.md").read_text() == "content\n", "must not write"


@pytest.mark.asyncio
async def test_action_note_rejects_facet_body(vault):
  root, registry = vault
  _write(root, "act.md", ACTION_NOTE)

  result = await _run(registry, note_id="act", body="clobber", facet="body")

  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "action note" in text
  assert "facet='description'" in text, "must suggest the working shape"
  assert (root / "act.md").read_text() == ACTION_NOTE, "must not write"


@pytest.mark.asyncio
async def test_unknown_facet_value_is_rejected(vault):
  _root, registry = vault
  result = await _run(registry, note_id="x", body="y", facet="recipe")

  assert result["isError"] is True
  assert "Invalid 'facet'" in result["content"][0]["text"]


# --- criterion 4: the happy path -------------------------------------


@pytest.mark.asyncio
async def test_action_note_description_rewrite_preserves_everything_else(vault):
  root, registry = vault
  _write(root, "act.md", ACTION_NOTE)

  result = await _run(
    registry,
    note_id="act",
    body="Play a [[music_theory/exercises/complete_this_scale_challenge]] "
    "then check it.",
    facet="description",
  )

  assert result.get("isError") is not True, result
  after = (root / "act.md").read_text()

  # Success text must not call an action note "vanilla" (the live smoke
  # caught exactly that), and must say what happened downstream.
  text = result["content"][0]["text"]
  assert "action note" in text
  assert "vanilla" not in text
  assert "out of date" in text

  # The wikilink actually moved.
  assert "[[music_theory/exercises/complete_this_scale_challenge]]" in after
  assert "[[exercises/complete_this_scale_challenge]]" not in after

  # Everything downstream survives byte-for-byte.
  recipe_onward = ACTION_NOTE[ACTION_NOTE.index("# Recipe") :]
  assert after.endswith(recipe_onward)

  # Frontmatter survives byte-for-byte, stamps included.
  frontmatter = ACTION_NOTE[: ACTION_NOTE.index("# Description")]
  assert after.startswith(frontmatter)


@pytest.mark.asyncio
async def test_description_rewrite_does_not_restamp_hexa_state(vault):
  """Criterion 8, resolved against step-1 findings.

  The prompt asked for source_facet/sync_state stamping here. forge-mcp
  has no stamp writer to reuse, and the plugin stamps reactively on
  edit + file-open — so writing stamps in Python would duplicate
  facet-hash-core.ts's hashing AND diverge from a hand-edit, which
  doesn't stamp either. The stamps must therefore be untouched; this
  test pins that so a later change is a deliberate one.
  """
  root, registry = vault
  _write(root, "act.md", ACTION_NOTE)

  await _run(registry, note_id="act", body="new prose", facet="description")

  after = (root / "act.md").read_text()
  for stamp in (
    "source_facet: python",
    "sync_state: stale-both",
    "description_hash: abc123",
    "recipe_hash: def456",
    "python_hash: 789abc",
  ):
    assert stamp in after, f"{stamp!r} must survive untouched"


# --- criterion 7: idempotence + structure ----------------------------


@pytest.mark.asyncio
async def test_repeated_description_edits_are_structurally_identical(vault):
  root, registry = vault
  _write(root, "act.md", ACTION_NOTE)

  await _run(registry, note_id="act", body="first", facet="description")
  once = (root / "act.md").read_text()
  await _run(registry, note_id="act", body="first", facet="description")
  twice = (root / "act.md").read_text()

  assert once == twice, "rewriting with the same text must be a no-op"
  assert once.count("# Recipe") == 1
  assert once.count("# Description") == 1


# --- criterion 6: auto-commit ----------------------------------------


@pytest.mark.asyncio
async def test_description_edit_auto_commits_with_facet_in_message(tmp_path):
  root = (tmp_path / "vault").resolve()
  root.mkdir()
  subprocess.run(["git", "init", "-q"], cwd=root, check=True)
  subprocess.run(
    ["git", "config", "user.email", "t@example.com"], cwd=root, check=True,
  )
  subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
  _write(root, "act.md", ACTION_NOTE)
  subprocess.run(["git", "add", "act.md"], cwd=root, check=True)
  subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

  registry = VaultRegistry({"main": VaultFS(root=root)})
  result = await _run(
    registry, note_id="act", body="rewritten", facet="description",
  )

  assert result.get("isError") is not True, result
  sha = result["structuredContent"]["git_sha"]
  assert sha, "expected a commit SHA"

  subject = subprocess.run(
    ["git", "log", "-1", "--format=%s"],
    cwd=root, check=True, capture_output=True, text=True,
  ).stdout.strip()
  assert subject == "forge_edit_markdown_note (facet=description): act"

  status = subprocess.run(
    ["git", "status", "--porcelain"],
    cwd=root, check=True, capture_output=True, text=True,
  ).stdout
  assert status == "", f"working tree should be clean, got {status!r}"


# --- the pure core ---------------------------------------------------


def test_rewrite_rejects_note_without_description_heading():
  with pytest.raises(DescriptionFacetError, match="no `# Description`"):
    rewrite_description_facet("---\ntype: action\n---\n\n# Recipe\n\nx\n", "y")


def test_rewrite_rejects_ambiguous_double_description():
  body = "# Description\n\na\n\n# Description\n\nb\n"
  with pytest.raises(DescriptionFacetError, match="refusing to guess"):
    rewrite_description_facet(body, "new")


def test_rewrite_handles_description_as_trailing_facet():
  out = rewrite_description_facet("---\ntype: action\n---\n\n# Description\n\nold\n", "new")
  assert out == "---\ntype: action\n---\n\n# Description\n\nnew\n"


def test_rewrite_does_not_swallow_a_legacy_e_dash_facet():
  body = "# Description\n\nold\n\n# E--\n\nlegacy\n"
  out = rewrite_description_facet(body, "new")
  assert "# E--\n\nlegacy\n" in out
  assert "old" not in out


def test_rewrite_clears_description_when_given_empty_text():
  out = rewrite_description_facet(ACTION_NOTE, "")
  assert "# Description\n\n\n# Recipe" in out
  assert "complete_this_scale_challenge" not in out
