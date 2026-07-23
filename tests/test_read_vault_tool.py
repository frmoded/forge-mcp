"""Drain CW-MCP-2-E — tests for the LOCAL VaultFS-backed
`forge_read_notes_in_vault` (rewritten from the Sprint 1 HTTP proxy).

Pre-drain this file tested the HTTP path (respx-mocked); the endpoint
never existed on forge-transpile, so the tool was silently broken.
Post-drain the tool walks the local vault directory via
`VaultFS.list_notes()` and returns `{note_id, name, path, has_recipe,
recipe_version}` per note. Test coverage per drain §5 (7 cases plus
supporting VaultFS unit + schema tests).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.schemas import VaultNoteEntry
from forge_mcp.tools import read_notes_in_vault
from forge_mcp.vault_fs import VaultFS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
  root = tmp_path / "vault"
  root.mkdir()
  return root


@pytest.fixture
def multi_note_vault(tmp_path: Path) -> Path:
  """Vault with 3 notes: two V2a-shaped (one with Recipe + version, one
  without Recipe), one hidden `.obsidian/config.md` that must be
  excluded from listings."""
  root = tmp_path / "vault"
  _write(root / "music" / "slow_burn.md", (
    "---\ntype: action\nrecipe_version: 3\n---\n\n"
    "# Description\n\nSlow blues.\n\n"
    "# Recipe\n\nReturn 42.\n"
  ))
  _write(root / "music" / "warm_up.md", (
    "---\ntype: action\n---\n\n"
    "# Description\n\nJust a description; no recipe yet.\n"
  ))
  _write(root / "moda" / "sim.md", (
    "---\ntype: action\nrecipe_version: 1\n---\n\n"
    "# Description\n\nx\n\n"
    "# Recipe\n\nReturn 0.\n"
  ))
  # Hidden-dir content that must be excluded.
  _write(root / ".obsidian" / "plugin-config.md", "hidden")
  _write(root / ".trash" / "junk.md", "junk")
  return root


# ---------------------------------------------------------------------------
# VaultFS.list_notes — drain §5 tests #1–#7
# ---------------------------------------------------------------------------


class TestListNotes:
  def test_list_notes_empty_vault_returns_empty(self, empty_vault: Path):
    """Drain §5 test #1."""
    fs = VaultFS(root=empty_vault)
    assert fs.list_notes() == []

  def test_list_notes_multi_note_returns_all_sorted(self, multi_note_vault: Path):
    """Drain §5 test #2 — 3 notes, sorted by note_id."""
    fs = VaultFS(root=multi_note_vault)
    entries = fs.list_notes()
    note_ids = [e["note_id"] for e in entries]
    assert note_ids == ["moda/sim", "music/slow_burn", "music/warm_up"]

  def test_list_notes_filter_substring(self, multi_note_vault: Path):
    """Drain §5 test #3 — substring filter on note_id."""
    fs = VaultFS(root=multi_note_vault)
    entries = fs.list_notes(filter="music")
    assert {e["note_id"] for e in entries} == {"music/slow_burn", "music/warm_up"}

  def test_list_notes_excludes_hidden_dirs(self, multi_note_vault: Path):
    """Drain §5 test #4 — `.obsidian/` and `.trash/` never appear."""
    fs = VaultFS(root=multi_note_vault)
    note_ids = {e["note_id"] for e in fs.list_notes()}
    assert not any(n.startswith(".obsidian") or n.startswith(".trash") for n in note_ids)
    assert "obsidian/plugin-config" not in note_ids  # not just prefix — no leak at all
    assert "trash/junk" not in note_ids

  def test_list_notes_populates_has_recipe_true(self, multi_note_vault: Path):
    """Drain §5 test #5 — note WITH `# Recipe` → has_recipe=True + version."""
    fs = VaultFS(root=multi_note_vault)
    entries = {e["note_id"]: e for e in fs.list_notes()}
    slow = entries["music/slow_burn"]
    assert slow["has_recipe"] is True
    assert slow["recipe_version"] == 3

  def test_list_notes_populates_has_recipe_false(self, multi_note_vault: Path):
    """Drain §5 test #6 — note WITHOUT `# Recipe` → has_recipe=False +
    recipe_version=None (never committed)."""
    fs = VaultFS(root=multi_note_vault)
    entries = {e["note_id"]: e for e in fs.list_notes()}
    warm = entries["music/warm_up"]
    assert warm["has_recipe"] is False
    assert warm["recipe_version"] is None

  def test_list_notes_tolerates_unparseable_notes(self, tmp_path: Path):
    """Drain §5 test #7 — non-V2a `.md` files (random prose, no
    frontmatter, no facet headers) still appear in the listing with
    has_recipe=False; do NOT crash the whole walk."""
    root = tmp_path / "vault"
    _write(root / "random.md", "Just plain markdown prose. No frontmatter. Nothing V2a.\n")
    _write(root / "valid.md", (
      "---\nrecipe_version: 5\n---\n\n# Recipe\n\nReturn 1.\n"
    ))
    fs = VaultFS(root=root)
    entries = {e["note_id"]: e for e in fs.list_notes()}
    assert "random" in entries
    assert entries["random"]["has_recipe"] is False
    assert entries["random"]["recipe_version"] is None
    # Valid note still parsed correctly.
    assert entries["valid"]["has_recipe"] is True
    assert entries["valid"]["recipe_version"] == 5


# ---------------------------------------------------------------------------
# Tool layer — dependency-injected VaultFS
# ---------------------------------------------------------------------------


class TestReadNotesInVaultTool:
  @pytest.mark.asyncio
  async def test_tool_returns_structured_content_on_success(self, multi_note_vault: Path):
    fs = VaultFS(root=multi_note_vault)
    result = await read_notes_in_vault.run(
      arguments={}, bearer="tok", vault_fs=fs,
    )
    assert result["isError"] is False
    notes = result["structuredContent"]["notes"]
    assert len(notes) == 3
    # Every returned entry must be reshaped correctly.
    # `sync_state` added in drain 2026-07-23-1700 Phase 1; None for
    # these fixture notes which don't carry the frontmatter field.
    for entry in notes:
      assert set(entry.keys()) == {
        "note_id", "name", "path", "has_recipe", "recipe_version", "sync_state",
      }

  @pytest.mark.asyncio
  async def test_tool_empty_vault_returns_empty_success(self, empty_vault: Path):
    fs = VaultFS(root=empty_vault)
    result = await read_notes_in_vault.run(
      arguments={}, bearer="tok", vault_fs=fs,
    )
    assert result["isError"] is False
    assert result["structuredContent"]["notes"] == []
    assert "No vault notes matched" in result["content"][0]["text"]

  @pytest.mark.asyncio
  async def test_tool_filter_passed_through(self, multi_note_vault: Path):
    fs = VaultFS(root=multi_note_vault)
    result = await read_notes_in_vault.run(
      arguments={"filter": "moda"}, bearer="tok", vault_fs=fs,
    )
    assert result["isError"] is False
    notes = result["structuredContent"]["notes"]
    assert len(notes) == 1
    assert notes[0]["note_id"] == "moda/sim"

  @pytest.mark.asyncio
  async def test_tool_missing_vault_returns_actionable_error(self, tmp_path: Path, monkeypatch):
    """If FORGE_VAULT_PATH points at a non-existent dir, we surface a
    clean isError message naming the env var — driver can fix without
    poking source."""
    monkeypatch.setenv("FORGE_VAULT_PATH", str(tmp_path / "does-not-exist"))
    result = await read_notes_in_vault.run(
      arguments={}, bearer="tok",  # no vault_fs injected → construct from env
    )
    assert result["isError"] is True
    assert "FORGE_VAULT_PATH" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Schema — regression on the reshape
# ---------------------------------------------------------------------------


class TestVaultNoteEntrySchema:
  def test_accepts_new_shape(self):
    entry = VaultNoteEntry(
      note_id="music/slow_burn",
      name="slow_burn",
      path="music/slow_burn.md",
      has_recipe=True,
      recipe_version=3,
    )
    assert entry.recipe_version == 3

  def test_recipe_version_defaults_to_none(self):
    entry = VaultNoteEntry(
      note_id="foo", name="foo", path="foo.md", has_recipe=False,
    )
    assert entry.recipe_version is None

  def test_pre_drain_fields_are_rejected(self):
    """Pre-drain shape (`state`, `source_facet`, `latest_recipe_version`)
    must now raise — model_config is extra='forbid'."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
      VaultNoteEntry(
        note_id="x", name="x", path="x.md", has_recipe=True,
        state="committed",  # type: ignore[call-arg]
        source_facet="recipe",  # type: ignore[call-arg]
        latest_recipe_version=1,  # type: ignore[call-arg]
      )

  # ---- Drain 2026-07-23-1700 Phase 1 — sync_state on VaultNoteEntry ----

  def test_sync_state_default_is_none(self):
    """New sync_state field defaults to None when not provided —
    matches the "field absent from frontmatter" case per drain 1700."""
    entry = VaultNoteEntry(
      note_id="foo", name="foo", path="foo.md", has_recipe=False,
    )
    assert entry.sync_state is None

  def test_sync_state_accepts_known_value(self):
    entry = VaultNoteEntry(
      note_id="foo", name="foo", path="foo.md", has_recipe=False,
      sync_state="stale-recipe",
    )
    assert entry.sync_state == "stale-recipe"

  def test_sync_state_accepts_unknown_value(self):
    """Typed as str | None (NOT Literal) so future Phase 2 states
    surface without erroring. Matches drain 1700 §4 B.4 rationale."""
    entry = VaultNoteEntry(
      note_id="foo", name="foo", path="foo.md", has_recipe=False,
      sync_state="future-phase-2-state",
    )
    assert entry.sync_state == "future-phase-2-state"


# ---------------------------------------------------------------------------
# Drain 2026-07-23-1700 Phase 1 — list_notes surfaces sync_state
# ---------------------------------------------------------------------------


class TestListNotesSyncState:
  """VaultFS.list_notes must populate sync_state from frontmatter for
  each note (or None when absent)."""

  def test_list_notes_returns_sync_state_when_present(self, tmp_path: Path):
    root = tmp_path / "vault"
    _write(root / "stale.md", (
      "---\ntype: action\nrecipe_version: 1\n"
      "sync_state: stale-recipe\n---\n\n"
      "# Description\n\nx\n\n# Recipe\n\nReturn 1.\n"
    ))
    _write(root / "pre_drain.md", (
      "---\ntype: action\n---\n\n"
      "# Description\n\ny\n"
    ))
    vault_fs = VaultFS(root=root)
    entries = vault_fs.list_notes()
    by_id = {e["note_id"]: e for e in entries}
    assert by_id["stale"]["sync_state"] == "stale-recipe"
    assert by_id["pre_drain"]["sync_state"] is None
