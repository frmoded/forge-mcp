"""Vault-split Phase 3 3d (drain 2026-08-06-0200) — `source_vault` +
`collides_with` on `forge_read_notes_in_vault`.

Local notes come first tagged with the calling vault's name; imported
vaults' notes follow in `[imports]` declaration order tagged with the
import NAME (never the on-disk path). Bare-name twins across sources
get `collides_with` so callers can disambiguate — the collision unit is
the bare name, the same unit bare wikilinks resolve by.
"""
from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from forge_mcp.tools import read_notes_in_vault
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


def _write(path: Path, content: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")


def _note(body: str = "hello") -> str:
  return f"---\ntype: action\n---\n\n# Description\n\n{body}\n"


def _make_vault(root: Path, name: str, extra_toml: str = "") -> Path:
  root.mkdir(parents=True, exist_ok=True)
  _write(root / "forge.toml", (
    f'name = "{name}"\nversion = "0.0.1"\n'
    'description = "test fixture vault"\ndomains = ["music"]\n'
    + extra_toml
  ))
  return root


@pytest.fixture
def music_with_import(tmp_path: Path) -> VaultRegistry:
  """`music` imports `music-core-fixture` (sibling dir, table syntax
  per drain 1900 §1.2)."""
  fixture = _make_vault(tmp_path / "music-core-fixture", "music-core-fixture")
  _write(fixture / "shared_note.md", _note("from the import"))
  _write(fixture / "core_only.md", _note("core"))

  music = _make_vault(
    tmp_path / "music", "music",
    '\n[imports]\nmusic-core-fixture = { local = "../music-core-fixture" }\n',
  )
  _write(music / "local_song.md", _note("local"))
  _write(music / "shared_note.md", _note("from the local vault"))

  return VaultRegistry({"music": VaultFS(root=music)})


async def _notes(reg: VaultRegistry, vault: str | None = "music") -> list[dict]:
  args: dict = {} if vault is None else {"vault": vault}
  result = await read_notes_in_vault.run(
    arguments=args, bearer="tok", vault_registry=reg,
  )
  assert result["isError"] is False
  jsonschema.validate(
    result["structuredContent"], read_notes_in_vault.OUTPUT_SCHEMA
  )
  return result["structuredContent"]["notes"]


@pytest.mark.asyncio
async def test_read_notes_returns_source_vault_for_local(
  music_with_import: VaultRegistry,
):
  notes = await _notes(music_with_import)
  local = [n for n in notes if n["note_id"] in ("local_song", "shared_note")
           and n["source_vault"] == "music"]
  assert {n["note_id"] for n in local} == {"local_song", "shared_note"}


@pytest.mark.asyncio
async def test_read_notes_returns_source_vault_for_imported(
  music_with_import: VaultRegistry,
):
  notes = await _notes(music_with_import)
  imported = [n for n in notes if n["source_vault"] == "music-core-fixture"]
  assert {n["note_id"] for n in imported} == {"shared_note", "core_only"}


@pytest.mark.asyncio
async def test_read_notes_ordering_local_then_imports(tmp_path: Path):
  """Two imports, declared b-then-a: listing is local, then import-b's
  notes, then import-a's — declaration order, not alphabetical."""
  imp_a = _make_vault(tmp_path / "imp-a", "imp-a")
  _write(imp_a / "a_note.md", _note())
  imp_b = _make_vault(tmp_path / "imp-b", "imp-b")
  _write(imp_b / "b_note.md", _note())
  music = _make_vault(
    tmp_path / "music", "music",
    '\n[imports]\nimp-b = { local = "../imp-b" }\n'
    'imp-a = { local = "../imp-a" }\n',
  )
  _write(music / "local_note.md", _note())
  reg = VaultRegistry({"music": VaultFS(root=music)})

  notes = await _notes(reg)
  assert [n["source_vault"] for n in notes] == ["music", "imp-b", "imp-a"]


@pytest.mark.asyncio
async def test_read_notes_flags_collision_across_local_and_import(
  music_with_import: VaultRegistry,
):
  notes = await _notes(music_with_import)
  by_source = {n["source_vault"]: n for n in notes if n["name"] == "shared_note"}
  assert set(by_source) == {"music", "music-core-fixture"}
  assert by_source["music"]["collides_with"] == ["music-core-fixture"]
  assert by_source["music-core-fixture"]["collides_with"] == ["music"]
  # Non-colliding notes stay clean on both sides.
  clean = {n["note_id"]: n for n in notes if n["name"] != "shared_note"}
  assert all(n["collides_with"] == [] for n in clean.values())


@pytest.mark.asyncio
async def test_read_notes_backwards_compat_no_imports(tmp_path: Path):
  """A vault with no [imports] returns local notes only — every entry
  tagged with the local name, collides_with always present + empty, so
  existing consumers see purely additive fields."""
  music = _make_vault(tmp_path / "music", "music")
  _write(music / "solo.md", _note())
  reg = VaultRegistry({"music": VaultFS(root=music)})

  notes = await _notes(reg, vault=None)  # default-vault path too
  assert [n["note_id"] for n in notes] == ["solo"]
  assert notes[0]["source_vault"] == "music"
  assert notes[0]["collides_with"] == []


@pytest.mark.asyncio
async def test_read_notes_source_vault_uses_import_name_not_path(
  tmp_path: Path,
):
  """Import key `corelib` points at a dir named `music-core-fixture`
  whose forge.toml name is also `corelib` (parse_imports requires
  key == declared name). Entries must carry the import NAME; no entry
  may leak the on-disk directory name or any path."""
  fixture = _make_vault(tmp_path / "music-core-fixture", "corelib")
  _write(fixture / "shared_note.md", _note())
  music = _make_vault(
    tmp_path / "music", "music",
    '\n[imports]\ncorelib = { local = "../music-core-fixture" }\n',
  )
  _write(music / "local_song.md", _note())
  reg = VaultRegistry({"music": VaultFS(root=music)})

  notes = await _notes(reg)
  sources = {n["source_vault"] for n in notes}
  assert sources == {"music", "corelib"}
  assert "music-core-fixture" not in sources
  assert not any("/" in n["source_vault"] for n in notes)
