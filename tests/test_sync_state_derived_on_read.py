"""sync_state is DERIVED on read and written by nobody.

Drain 2026-08-17-0100 — Phase 2 of the Option C retirement. Phase 1
(forge `3a53ed2`) shipped `derive_sync_state`; this switches the two
forge-mcp read surfaces onto it and removes forge-mcp's two writers.

Non-vacuity is the whole point, so it is asserted rather than narrated:
each incident note below is a VERBATIM frontmatter from the paper's
trust failures, and each is asserted to come back from the READ SURFACE
with a value that differs from the lie stored in the file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp._vendor.sync_state import derive_sync_state
from forge_mcp.tools import commit_recipe, create_note, read_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.fixture
def registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"music": VaultFS(root=vault)})


def _write(registry: VaultRegistry, note_id: str, frontmatter: str) -> Path:
  path = registry.get("music").root / f"{note_id}.md"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    f"---\n{frontmatter.strip()}\n---\n\n# Description\n\nD.\n\n"
    f"# Recipe\n\nReturn 1.\n"
  )
  return path


# --- the incidents, verbatim (Phase 1 FEEDBACK §3) --------------------

MURMURATION = """
type: action
description_hash: 8aaecfa415bb7256066f21225870611353cd418387b45e6b1dd258eb4f996af3
recipe_hash: 788c6cce4d86865b1313053e5e7dcd3dd11139f080b9ba86a1adc1476c257df8
python_hash: 4dbc6dd03755d44539a0328354ccdb1973e3127e17f0ee52e1503aea7cebfbb8
source_facet: description
recipe_derived_from_description_hash: 8aaecfa415bb7256066f21225870611353cd418387b45e6b1dd258eb4f996af3
python_derived_from_recipe_hash: 788c6cce4d86865b1313053e5e7dcd3dd11139f080b9ba86a1adc1476c257df8
sync_state: stale-recipe
"""

SOLITARY = f"""
type: action
source_facet: description
sync_state: stale-recipe
description_hash: ff4f50ccd1a7dc0477b0fa9a22bca658f89d18bf5c30c02c9192f7f4b87ac137
recipe_hash: 33e0149c82c810a5313da479fb3abd6982e6d422a41f2e6c0220137cc65a89d2
python_hash: {EMPTY}
recipe_derived_from_description_hash: ff4f50ccd1a7dc0477b0fa9a22bca658f89d18bf5c30c02c9192f7f4b87ac137
python_derived_from_recipe_hash: {EMPTY}
"""

QUIZ_PRE_BACKFILL = """
type: action
sync_state: synced
"""

QUIZ_POST_BACKFILL = """
type: action
description_hash: dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
recipe_hash: rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr
python_hash: pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp
recipe_derived_from_source_hash: dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
sync_state: synced
"""

STEP5_POST_RUN = """
type: action
description_hash: f492eeba3db3ae9924a20d5f3c670a1d8b56983cbfd6c91eae7f4149b0acc684
recipe_hash: dcdee86b473f8b8222f55ab9959444837980b67bdb5eaef48277b7b62ea148a6
python_hash: f27d0b1b851839a42dc761273fea81f5bdeac679c7ec8d4036c2977610fe1867
source_facet: recipe
recipe_derived_from_description_hash: f492eeba3db3ae9924a20d5f3c670a1d8b56983cbfd6c91eae7f4149b0acc684
python_derived_from_recipe_hash: dcdee86b473f8b8222f55ab9959444837980b67bdb5eaef48277b7b62ea148a6
"""

INCIDENTS = [
  ("murmuration", MURMURATION, "stale-recipe", "synced"),
  ("solitary", SOLITARY, "stale-recipe", "stale-python"),
  ("quiz_pre_backfill", QUIZ_PRE_BACKFILL, "synced", "unknown"),
  ("quiz_post_backfill", QUIZ_POST_BACKFILL, "synced", "stale-python"),
  ("step5_post_run", STEP5_POST_RUN, None, "synced"),
]


# --- readers ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("name,fm,stored,derived", INCIDENTS)
async def test_read_note_reports_the_derived_value(
  registry: VaultRegistry, name: str, fm: str, stored, derived: str,
):
  """§4's incident regression, through the READ SURFACE, not the function."""
  _write(registry, name, fm)
  result = await read_note.run(
    arguments={"note_id": name}, bearer="tok", vault_registry=registry,
  )
  got = result["structuredContent"]["note"]["sync_state"]

  assert got == derived
  assert got != stored, (
    f"{name}: the read surface still reports the stored value {stored!r}"
  )


@pytest.mark.asyncio
async def test_list_notes_reports_the_derived_value(registry: VaultRegistry):
  for name, fm, _stored, _derived in INCIDENTS:
    _write(registry, name, fm)
  entries = {e["note_id"]: e for e in registry.get("music").list_notes()}

  for name, _fm, stored, derived in INCIDENTS:
    assert entries[name]["sync_state"] == derived, name
    assert entries[name]["sync_state"] != stored, name


@pytest.mark.asyncio
async def test_a_note_with_no_frontmatter_stamps_reads_unknown_not_none(
  registry: VaultRegistry,
):
  """The schema told callers to treat None as 'unknown' and never infer
  'synced'. Now the value says so itself."""
  _write(registry, "bare", "type: action")
  result = await read_note.run(
    arguments={"note_id": "bare"}, bearer="tok", vault_registry=registry,
  )
  assert result["structuredContent"]["note"]["sync_state"] == "unknown"


def test_readers_do_not_reimplement_the_vocabulary(registry: VaultRegistry):
  """§8 — consumers CALL the engine function. If vault_fs grew its own
  copy of the rules, this comparison would be the thing that still
  passed while the two drifted, so compare against the vendored source
  of truth for every incident."""
  for name, fm, _stored, derived in INCIDENTS:
    path = _write(registry, name, fm)
    parsed = registry.get("music").read_note_content(name)
    assert parsed["sync_state"] == derived == derive_sync_state(
      parsed["frontmatter"]
    ), path


# --- writers ----------------------------------------------------------


@pytest.mark.asyncio
async def test_create_note_shell_no_longer_stamps_sync_state(
  registry: VaultRegistry,
):
  await create_note.run(
    arguments={"note_id": "fresh", "description": "Fresh."},
    bearer="tok", vault_registry=registry,
  )
  raw = (registry.get("music").root / "fresh.md").read_text()
  assert "sync_state" not in raw, "the note shell is still a writer"


@pytest.mark.asyncio
async def test_commit_recipe_no_longer_stamps_sync_state(
  registry: VaultRegistry,
):
  await create_note.run(
    arguments={"note_id": "commits", "description": "D."},
    bearer="tok", vault_registry=registry,
  )
  await commit_recipe.run(
    arguments={
      "note_id": "commits", "source": "Return 1.", "expected_version": 0,
    },
    bearer="tok", vault_registry=registry,
  )
  raw = (registry.get("music").root / "commits.md").read_text()
  assert "sync_state" not in raw, "commit_recipe is still a writer"


@pytest.mark.asyncio
async def test_an_existing_stored_field_is_left_alone_but_ignored(
  registry: VaultRegistry,
):
  """Phase 3 strips the field; Phase 2 only stops believing it. A commit
  must neither update nor delete what is already on disk."""
  path = _write(registry, "legacy", MURMURATION)
  await commit_recipe.run(
    arguments={"note_id": "legacy", "source": "Return 2."},
    bearer="tok", vault_registry=registry,
  )
  raw = path.read_text()
  assert "sync_state: stale-recipe" in raw, "Phase 3 owns removal, not this one"

  result = await read_note.run(
    arguments={"note_id": "legacy"}, bearer="tok", vault_registry=registry,
  )
  assert result["structuredContent"]["note"]["sync_state"] != "stale-recipe"
