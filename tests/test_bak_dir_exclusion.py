"""TDD failing-test-first — drain 2026-08-25-1050.

forge-mcp's vault walks skip any path segment starting with `.`, which
covers `.forge/`, `.obsidian/`, `.git/`. It does NOT cover BACKUP dirs,
whose names do not start with a dot — `forge-moda.bak.previous/`.

That was harmless while backup dirs did not exist: v0.2.106 deleted the
outgoing tree on re-extract. Drain 2026-08-25-0120 changed that — every
re-extract now leaves exactly one `<vault>.bak.previous/` per vault, by
design. So this gap became live three drains ago.

Consequences, both of which the engine's own `_BAK_DIR_PATTERN` exists
to prevent:
  * `list_notes` surfaces every backup note as a real note.
  * bare-name wikilink resolution rglobs the whole vault, so a backup
    copy is a candidate — the shadowing class behind
    AmbiguousSnippetResolutionError.
"""
import pytest
from forge_mcp.vault_fs import VaultFS


@pytest.fixture
def vault(tmp_path):
  (tmp_path / "forge.toml").write_text('version = "0.1.0"\n')
  (tmp_path / "notes").mkdir()
  (tmp_path / "notes" / "hello.md").write_text("---\ntype: action\n---\n\n# Recipe\n\nReturn 1.\n")
  bak = tmp_path / "forge-moda.bak.previous" / "notes"
  bak.mkdir(parents=True)
  (bak / "hello.md").write_text("---\ntype: action\n---\n\n# Recipe\n\nReturn 2.\n")
  hidden = tmp_path / ".forge" / "edges"
  hidden.mkdir(parents=True)
  (hidden / "snap.md").write_text("---\ntype: snapshot\n---\n")
  return VaultFS(tmp_path)


def test_non_vacuity_the_fixture_really_contains_a_backup_twin(vault):
  """If the backup note stops existing, the assertions below prove
  nothing — they would pass over an empty tree."""
  hits = list(vault.root.rglob("hello.md"))
  assert len(hits) == 2, f"fixture must have a real note AND a backup twin, got {hits}"


def test_list_notes_excludes_backup_dirs(vault):
  ids = {n["note_id"] for n in vault.list_notes()}
  assert "notes/hello" in ids, "the real note must still be listed"
  assert not any(".bak." in i for i in ids), f"backup notes leaked into the listing: {ids}"


def test_list_notes_still_excludes_hidden_dirs(vault):
  """The pre-existing dot rule must survive the change."""
  ids = {n["note_id"] for n in vault.list_notes()}
  assert not any(i.startswith(".forge") for i in ids), ids
