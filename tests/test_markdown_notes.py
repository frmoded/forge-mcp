"""CW-mcp-and-plugin-support-vanilla-notes — vanilla markdown note tests.

Drain 2026-07-26-1200. Covers:
  1. forge_create_markdown_note happy path — body written verbatim.
  2. Empty body allowed.
  3. Nested path — parent dirs created.
  4. Refuses to overwrite an existing file.
  5. forge_edit_markdown_note happy path — full-body replace.
  6. forge_edit_markdown_note refuses action notes.
  7. forge_edit_markdown_note refuses data notes.
  8. forge_edit_markdown_note refuses missing notes.
  9. forge_read_note returns type='vanilla' for a vanilla note.
 10. forge_read_note returns type='action' for a Forge action note.
 11. forge_read_note returns type='data' for a data note.
 12. forge_read_notes_in_vault surfaces `type` per entry, including vanilla.
 13. Path-traversal rejected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import (
  create_markdown_note,
  create_note,
  edit_markdown_note,
  read_note,
  read_notes_in_vault,
)
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"music": VaultFS(root=vault)})


# ---------------------------------------------------------------------------
# forge_create_markdown_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_markdown_note_writes_body_verbatim(
  single_vault_registry: VaultRegistry,
):
  """Acceptance criterion §2 — body written byte-for-byte, no
  frontmatter auto-injection."""
  body = "# Scale\n\nA scale is a sequence of notes.\n"
  result = await create_markdown_note.run(
    arguments={
      "note_id": "music_theory/test_vanilla",
      "body": body,
      "vault": "music",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "music_theory" / "test_vanilla.md"
  assert path.is_file()
  assert path.read_text() == body
  # NO frontmatter, NO `type: action` scaffolding.
  assert not path.read_text().startswith("---")
  assert "type: action" not in path.read_text()


@pytest.mark.asyncio
async def test_create_markdown_note_empty_body(
  single_vault_registry: VaultRegistry,
):
  """Empty body allowed — writes zero-byte file."""
  result = await create_markdown_note.run(
    arguments={"note_id": "blank"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "blank.md"
  assert path.is_file()
  assert path.read_text() == ""


@pytest.mark.asyncio
async def test_create_markdown_note_nested_path(
  single_vault_registry: VaultRegistry,
):
  """Parent dirs materialize as a side effect (like create_note)."""
  result = await create_markdown_note.run(
    arguments={
      "note_id": "music_theory/basics/interval",
      "body": "Interval prose.",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get("music")
  assert (
    vault_fs.root / "music_theory" / "basics" / "interval.md"
  ).is_file()


@pytest.mark.asyncio
async def test_create_markdown_note_refuses_overwrite(
  single_vault_registry: VaultRegistry,
):
  """Second create fails cleanly, existing content untouched."""
  await create_markdown_note.run(
    arguments={"note_id": "occupied", "body": "first"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await create_markdown_note.run(
    arguments={"note_id": "occupied", "body": "second"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "already exists" in result["content"][0]["text"].lower()
  vault_fs = single_vault_registry.get("music")
  assert (vault_fs.root / "occupied.md").read_text() == "first"


@pytest.mark.asyncio
async def test_create_markdown_note_rejects_path_traversal(
  single_vault_registry: VaultRegistry,
):
  """Path-traversal rejected before any write."""
  result = await create_markdown_note.run(
    arguments={"note_id": "../../etc/passwd", "body": "x"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  vault_fs = single_vault_registry.get("music")
  assert list(vault_fs.root.iterdir()) == []


# ---------------------------------------------------------------------------
# forge_edit_markdown_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_markdown_note_replaces_body(
  single_vault_registry: VaultRegistry,
):
  """Acceptance criterion §3 — full-body replace on a vanilla note."""
  await create_markdown_note.run(
    arguments={
      "note_id": "music_theory/test_vanilla",
      "body": "# Scale\n\nA scale is a sequence of notes.\n",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  new_body = "# Scale (revised)\n\nA scale is an ordered sequence of pitches.\n"
  result = await edit_markdown_note.run(
    arguments={
      "note_id": "music_theory/test_vanilla",
      "body": new_body,
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  vault_fs = single_vault_registry.get("music")
  path = vault_fs.root / "music_theory" / "test_vanilla.md"
  assert path.read_text() == new_body


@pytest.mark.asyncio
async def test_edit_markdown_note_refuses_action_note(
  single_vault_registry: VaultRegistry,
):
  """Acceptance criterion §4 — action notes must go through
  forge_commit_recipe, not forge_edit_markdown_note."""
  # Create an action note via forge_create_note (writes `type: action`).
  await create_note.run(
    arguments={
      "note_id": "experiments/hello_world",
      "description": "Say hi",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await edit_markdown_note.run(
    arguments={
      "note_id": "experiments/hello_world",
      "body": "attempted overwrite",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  msg = result["content"][0]["text"].lower()
  assert "action note" in msg
  assert "forge_commit_recipe" in msg
  # Original content preserved.
  vault_fs = single_vault_registry.get("music")
  content = (vault_fs.root / "experiments" / "hello_world.md").read_text()
  assert "type: action" in content
  assert "attempted overwrite" not in content


@pytest.mark.asyncio
async def test_edit_markdown_note_refuses_data_note(
  single_vault_registry: VaultRegistry,
):
  """Data notes are out of scope for this tool per drain §Not in scope."""
  vault_fs = single_vault_registry.get("music")
  data_path = vault_fs.root / "sample_state.md"
  data_path.write_text(
    "---\ntype: data\n---\n\n# Data\n\nfoo: bar\n"
  )
  result = await edit_markdown_note.run(
    arguments={"note_id": "sample_state", "body": "attempted overwrite"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "data note" in result["content"][0]["text"].lower()
  # Original content preserved.
  assert "type: data" in data_path.read_text()
  assert "attempted overwrite" not in data_path.read_text()


@pytest.mark.asyncio
async def test_edit_markdown_note_refuses_missing_note(
  single_vault_registry: VaultRegistry,
):
  """Trying to edit a non-existent note surfaces the create tool as a hint."""
  result = await edit_markdown_note.run(
    arguments={"note_id": "nonexistent", "body": "content"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "forge_create_markdown_note" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# forge_read_note discriminated `type` field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_note_types_vanilla(single_vault_registry: VaultRegistry):
  """Acceptance criterion §5 — vanilla notes surface with type='vanilla'."""
  await create_markdown_note.run(
    arguments={
      "note_id": "music_theory/test_vanilla",
      "body": "# Scale\n\nProse content.\n",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await read_note.run(
    arguments={"note_id": "music_theory/test_vanilla"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note"]["type"] == "vanilla"
  assert result["structuredContent"]["note"]["recipe"] is None
  # Raw survives verbatim.
  assert result["structuredContent"]["note"]["raw"] == "# Scale\n\nProse content.\n"


@pytest.mark.asyncio
async def test_read_note_types_action(single_vault_registry: VaultRegistry):
  """forge_read_note surfaces action notes with type='action'."""
  await create_note.run(
    arguments={"note_id": "act", "description": "hi"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  result = await read_note.run(
    arguments={"note_id": "act"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note"]["type"] == "action"


@pytest.mark.asyncio
async def test_read_note_types_data(single_vault_registry: VaultRegistry):
  """forge_read_note surfaces data notes with type='data'."""
  vault_fs = single_vault_registry.get("music")
  (vault_fs.root / "state.md").write_text(
    "---\ntype: data\n---\n\n# Data\n\nfoo: bar\n"
  )
  result = await read_note.run(
    arguments={"note_id": "state"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note"]["type"] == "data"


# ---------------------------------------------------------------------------
# forge_read_notes_in_vault `type` per entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_notes_in_vault_surfaces_type_per_entry(
  single_vault_registry: VaultRegistry,
):
  """Acceptance criterion §6 — vault listing includes `type` for each
  note, discriminating vanilla / action / data."""
  vault_fs = single_vault_registry.get("music")
  # Vanilla note.
  await create_markdown_note.run(
    arguments={
      "note_id": "music_theory/test_vanilla",
      "body": "prose",
    },
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  # Action note.
  await create_note.run(
    arguments={"note_id": "act", "description": "hi"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  # Data note.
  (vault_fs.root / "state.md").write_text(
    "---\ntype: data\n---\n\n# Data\n\nfoo: bar\n"
  )

  result = await read_notes_in_vault.run(
    arguments={"vault": "music"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  entries = {n["note_id"]: n for n in result["structuredContent"]["notes"]}
  assert entries["music_theory/test_vanilla"]["type"] == "vanilla"
  assert entries["act"]["type"] == "action"
  assert entries["state"]["type"] == "data"


@pytest.mark.asyncio
async def test_read_notes_in_vault_unknown_type_falls_back_to_vanilla(
  single_vault_registry: VaultRegistry,
):
  """Frontmatter with `type: something_new` collapses to vanilla —
  future/typo values fail safely (won't trigger action-note pipelines)."""
  vault_fs = single_vault_registry.get("music")
  (vault_fs.root / "future.md").write_text(
    "---\ntype: newshape\n---\n\nBody.\n"
  )
  result = await read_notes_in_vault.run(
    arguments={"vault": "music"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  entries = {n["note_id"]: n for n in result["structuredContent"]["notes"]}
  assert entries["future"]["type"] == "vanilla"
