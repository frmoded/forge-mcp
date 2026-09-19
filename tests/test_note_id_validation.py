"""Drain 2026-09-19-0800 — `note_id` validator rejects literal parentheses.

Driver renamed two `music-theory` notes to `very_nice_(harmony).md` /
`nice_(fifths).md` (2026-09-18, deliberate literal naming — parens are
valid Obsidian filename characters). `forge_read_note` rejected both
outright: files are fine on disk, Obsidian resolves the wikilinks
correctly — this was a tool-side validation gap, not a vault-data
problem.

`_NOTE_ID_SEGMENT` (`vault_fs.py`) is the single choke point shared by
`_validate_note_id` (note tools) AND `_validate_dir_path` (mkdir
targets, per that function's own docstring: "Same segment allowlist
as note_id") — one regex, two validators, both covered here.
"""
from __future__ import annotations

import pytest

from forge_mcp.vault_fs import (
  DirInvalid,
  NoteIdInvalid,
  _validate_dir_path,
  _validate_note_id,
)


# ---------- the driver's exact reported case ------------------------------

def test_note_id_with_parens_is_accepted():
  # Must not raise.
  _validate_note_id("physics/Nice_And_Ugly/very_nice_(harmony)")
  _validate_note_id("physics/Nice_And_Ugly/nice_(fifths)")


def test_dir_path_with_parens_is_accepted():
  # Same shared regex — mkdir targets get the same leniency.
  _validate_dir_path("physics/Nice_And_Ugly_(draft)")


# ---------- negative controls: existing guards must still fire -----------

@pytest.mark.parametrize("note_id", [
  "../escape",
  "a/../b",
  "/absolute",
  "a//b",
  ".hidden",
  "a/.hidden",
  "vault.bak.previous/note",
])
def test_note_id_guards_still_reject(note_id: str):
  with pytest.raises(NoteIdInvalid):
    _validate_note_id(note_id)


@pytest.mark.parametrize("path", [
  "../escape",
  "a/../b",
  "/absolute",
  ".git",
  "a/.hidden",
])
def test_dir_path_guards_still_reject(path: str):
  with pytest.raises(DirInvalid):
    _validate_dir_path(path)


def test_note_id_still_rejects_other_unsupported_characters():
  # Parens are the ONLY new addition — everything else stays rejected.
  with pytest.raises(NoteIdInvalid):
    _validate_note_id("note with $dollar")
  with pytest.raises(NoteIdInvalid):
    _validate_note_id("note*star")
