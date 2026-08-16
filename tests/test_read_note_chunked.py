"""Drain 2026-08-14-2120 — forge_read_note offset/limit chunked read.

Wizard could not read `forge-wizard-protocol.md` (73,303 chars) at all: the
response exceeded the MCP client's token cap and there was no way to ask for a
smaller window. The cap is enforced downstream, outside this repo — read_note
itself had no length logic whatsoever — so the fix is pagination that never
existed, not repairing a truncation bug.

Chunking applies to `raw` ONLY. Re-parsing frontmatter out of a partial slice
would produce a structurally invalid note, and the derived facets are small
relative to the raw body that blows the budget.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_mcp.tools import read_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry

_BODY = "lorem ipsum dolor sit amet " * 400


@pytest.fixture
def vault(tmp_path: Path) -> VaultRegistry:
  (tmp_path / "big.md").write_text(
    "---\ntype: vanilla\n---\n\n# Description\n\n" + _BODY
  )
  (tmp_path / "small.md").write_text("---\ntype: vanilla\n---\n\n# Description\n\nhi\n")
  return VaultRegistry({"default": VaultFS(root=tmp_path)})


async def _read(vault, note_id="big", **extra):
  args = {"note_id": note_id}
  args.update(extra)
  r = await read_note.run(arguments=args, bearer="t", vault_registry=vault)
  assert r["isError"] is False, r
  return r["structuredContent"]["note"]


# -- backward compatibility -------------------------------------------------


@pytest.mark.asyncio
async def test_default_call_is_unchanged_against_a_pre_drain_snapshot(vault):
  """§5's non-vacuous regression guard: assert against output captured from
  the PRE-drain implementation, not merely 'returns something'."""
  snap = Path("/tmp/read_note_pre.json")
  if not snap.is_file():
    pytest.skip("pre-drain snapshot not captured in this environment")
  before = json.loads(snap.read_text())["note"]
  after = await _read(vault)
  # Drain 2026-08-17-0100 (Phase 2) — `sync_state` is deliberately
  # excluded. This snapshot pins the CHUNKING drain's contract; Phase 2
  # changed the field from a pass-through of the stored value to one
  # derived from the note's lineage, so `None -> "unknown"` here is the
  # intended change, not a chunking regression. Every other key must
  # still match byte-for-byte.
  for key, value in before.items():
    if key == "sync_state":
      continue
    assert after[key] == value, f"{key} changed: {value!r} -> {after[key]!r}"


@pytest.mark.asyncio
async def test_default_call_returns_the_whole_raw_body(vault):
  note = await _read(vault)
  assert note["raw"].endswith(_BODY[-40:])
  assert note["truncated"] is False
  assert note["total_length"] == len(note["raw"])


# -- slicing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_offset_zero_limit_100(vault):
  full = (await _read(vault))["raw"]
  note = await _read(vault, offset=0, limit=100)
  assert note["raw"] == full[:100]
  assert len(note["raw"]) == 100
  assert note["truncated"] is True
  assert note["total_length"] == len(full)


@pytest.mark.asyncio
async def test_offset_moves_the_window(vault):
  full = (await _read(vault))["raw"]
  note = await _read(vault, offset=100, limit=50)
  assert note["raw"] == full[100:150]


@pytest.mark.asyncio
async def test_offset_past_end_is_empty_not_an_error(vault):
  """§5 — empty slice, truncated False (nothing left), NOT an error."""
  full = (await _read(vault))["raw"]
  note = await _read(vault, offset=len(full) + 500, limit=100)
  assert note["raw"] == ""
  assert note["truncated"] is False
  assert note["total_length"] == len(full)


@pytest.mark.asyncio
async def test_limit_beyond_remaining_returns_the_rest(vault):
  full = (await _read(vault))["raw"]
  note = await _read(vault, offset=len(full) - 10, limit=9999)
  assert note["raw"] == full[-10:]
  assert note["truncated"] is False


@pytest.mark.asyncio
async def test_offset_without_limit_returns_to_the_end(vault):
  full = (await _read(vault))["raw"]
  note = await _read(vault, offset=50)
  assert note["raw"] == full[50:]
  assert note["truncated"] is False


# -- the test that actually proves the mechanism ----------------------------


@pytest.mark.asyncio
async def test_chunked_round_trip_reassembles_exactly(vault):
  """§5's end-to-end proof: walk the note in fixed-size chunks and assert the
  concatenation equals a single unchunked read."""
  full = (await _read(vault))["raw"]
  chunk = 512
  pieces, offset, guard = [], 0, 0
  while True:
    guard += 1
    assert guard < 100, "chunk loop failed to terminate"
    note = await _read(vault, offset=offset, limit=chunk)
    pieces.append(note["raw"])
    if not note["truncated"]:
      break
    offset += chunk
  assert "".join(pieces) == full
  assert len(pieces) > 1, "fixture too small to exercise multiple chunks"


# -- facets are deliberately NOT chunked ------------------------------------


@pytest.mark.asyncio
async def test_derived_facets_stay_whole_when_raw_is_sliced(vault):
  """The documented asymmetry — parsing a partial slice would yield a
  structurally invalid note."""
  whole = await _read(vault)
  sliced = await _read(vault, offset=0, limit=10)
  assert len(sliced["raw"]) == 10
  for key in ("frontmatter", "description", "type", "inputs"):
    assert sliced[key] == whole[key], key


# -- validation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_offset_errors(vault):
  r = await read_note.run(
    arguments={"note_id": "big", "offset": -1}, bearer="t", vault_registry=vault
  )
  assert r["isError"] is True


@pytest.mark.asyncio
async def test_zero_or_negative_limit_errors(vault):
  for bad in (0, -5):
    r = await read_note.run(
      arguments={"note_id": "big", "limit": bad}, bearer="t", vault_registry=vault
    )
    assert r["isError"] is True, bad


@pytest.mark.asyncio
async def test_schema_declares_the_new_params():
  props = read_note.INPUT_SCHEMA["properties"]
  assert props["offset"]["type"] == "integer"
  assert "limit" in props
  note_out = read_note.OUTPUT_SCHEMA["properties"]["note"]["properties"]
  assert "truncated" in note_out
  assert "total_length" in note_out


@pytest.mark.asyncio
async def test_description_documents_the_asymmetry():
  """§4 — callers must not be surprised that raw chunks but facets don't."""
  d = read_note.DESCRIPTION.lower()
  assert "offset" in d and "limit" in d
  assert "raw" in d
