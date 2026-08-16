"""CW-MCP-read-note — forge_read_note tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import read_note
from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_registry import VaultRegistry


def _write(root: Path, note_id: str, content: str) -> Path:
  path = root / f"{note_id}.md"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")
  return path


@pytest.fixture
def single_vault_registry(tmp_path: Path) -> VaultRegistry:
  vault = tmp_path / "vault"
  vault.mkdir()
  return VaultRegistry({"default": VaultFS(root=vault)})


@pytest.fixture
def two_vault_registry(tmp_path: Path) -> tuple[VaultRegistry, Path, Path]:
  a = tmp_path / "alpha"
  b = tmp_path / "beta"
  a.mkdir()
  b.mkdir()
  return (
    VaultRegistry({"alpha": VaultFS(root=a), "beta": VaultFS(root=b)}),
    a,
    b,
  )


FULL_NOTE = """---
recipe_version: 3
inputs: [n, tempo]
type: action
---

# Description

A shuffle in F minor. Uses two library-note calls.

# Recipe

Let bass = Call [[walking_bass_line]] with harmony=h, style="swing".
Return bass.

# Python

```python
def compute(context):
  return bass_helper()
```

# Data

hello: world
"""


@pytest.mark.asyncio
async def test_reads_full_v2a_note(single_vault_registry: VaultRegistry):
  """Drain §5 test #1 — happy path with all facets present."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "shuffle_f", FULL_NOTE)
  result = await read_note.run(
    arguments={"note_id": "shuffle_f"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  note = result["structuredContent"]["note"]
  assert note["note_id"] == "shuffle_f"
  assert note["vault"] == "default"
  assert "walking_bass_line" in (note["recipe"] or "")
  assert "def compute" in (note["python"] or "")
  assert "hello: world" in (note["data"] or "")
  assert "shuffle in F minor" in note["description"]


@pytest.mark.asyncio
async def test_reads_partial_note(single_vault_registry: VaultRegistry):
  """Drain §5 test #2 — only Description + Recipe; python/data are None."""
  vault_fs = single_vault_registry.get()
  content = (
    "---\nrecipe_version: 1\n---\n\n"
    "# Description\n\nJust a description.\n\n"
    "# Recipe\n\nReturn 42.\n"
  )
  _write(vault_fs.root, "partial", content)
  result = await read_note.run(
    arguments={"note_id": "partial"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  note = result["structuredContent"]["note"]
  assert note["recipe"] is not None
  assert note["python"] is None
  assert note["data"] is None


@pytest.mark.asyncio
async def test_returns_frontmatter_dict(single_vault_registry: VaultRegistry):
  """Drain §5 test #3 — YAML frontmatter parsed to dict of scalars."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "shuffle_f", FULL_NOTE)
  result = await read_note.run(
    arguments={"note_id": "shuffle_f"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  fm = result["structuredContent"]["note"]["frontmatter"]
  assert fm["recipe_version"] == "3"
  assert fm["type"] == "action"


@pytest.mark.asyncio
async def test_extracts_inputs_from_frontmatter(
  single_vault_registry: VaultRegistry,
):
  """Drain §5 test #4 — `inputs: [x, y]` in frontmatter becomes a list."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "shuffle_f", FULL_NOTE)
  result = await read_note.run(
    arguments={"note_id": "shuffle_f"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  inputs = result["structuredContent"]["note"]["inputs"]
  assert inputs == ["n", "tempo"]


@pytest.mark.asyncio
async def test_extracts_inputs_from_description_fallback(
  single_vault_registry: VaultRegistry,
):
  """Legacy shape: `Inputs: a, b` line in Description body."""
  vault_fs = single_vault_registry.get()
  content = (
    "---\n---\n\n"
    "# Description\n\nSome prose.\n\nInputs: alpha, beta\n\n"
    "# Recipe\n\nReturn 1.\n"
  )
  _write(vault_fs.root, "legacy_inputs", content)
  result = await read_note.run(
    arguments={"note_id": "legacy_inputs"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  inputs = result["structuredContent"]["note"]["inputs"]
  assert inputs == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_returns_raw_source(single_vault_registry: VaultRegistry):
  """Drain §5 test #5 — full markdown available verbatim."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "shuffle_f", FULL_NOTE)
  result = await read_note.run(
    arguments={"note_id": "shuffle_f"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  raw = result["structuredContent"]["note"]["raw"]
  assert raw == FULL_NOTE


@pytest.mark.asyncio
async def test_rejects_missing_note(single_vault_registry: VaultRegistry):
  """Drain §5 test #6 — clean isError, not filesystem exception."""
  result = await read_note.run(
    arguments={"note_id": "does_not_exist"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True
  assert "not found" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_rejects_path_traversal(single_vault_registry: VaultRegistry):
  """Drain §5 test #7 — `../etc/passwd` refused."""
  result = await read_note.run(
    arguments={"note_id": "../etc/passwd"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_targets_named_vault_when_multi(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """Drain §5 test #8 — vault=X reads from X, not the default."""
  reg, a, b = two_vault_registry
  # Same note_id in both vaults; content differs.
  _write(a, "shared", "---\n---\n# Description\n\nalpha vault\n")
  _write(b, "shared", "---\n---\n# Description\n\nbeta vault\n")
  result = await read_note.run(
    arguments={"note_id": "shared", "vault": "beta"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is False
  assert "beta vault" in result["structuredContent"]["note"]["description"]


@pytest.mark.asyncio
async def test_defaults_to_first_vault_when_omitted(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  """No vault arg → first-registered (alpha)."""
  reg, a, b = two_vault_registry
  _write(a, "only_in_alpha", "---\n---\n# Description\n\nfrom alpha\n")
  result = await read_note.run(
    arguments={"note_id": "only_in_alpha"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note"]["vault"] == "alpha"


@pytest.mark.asyncio
async def test_unknown_vault_returns_error(
  two_vault_registry: tuple[VaultRegistry, Path, Path],
):
  reg, _, _ = two_vault_registry
  result = await read_note.run(
    arguments={"note_id": "x", "vault": "gamma"},
    bearer="tok",
    vault_registry=reg,
  )
  assert result["isError"] is True
  assert "not registered" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_normalizes_md_suffix(single_vault_registry: VaultRegistry):
  """Trailing .md tolerated in note_id; response strips it."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "trailing", "---\n---\n# Description\n\nok\n")
  result = await read_note.run(
    arguments={"note_id": "trailing.md"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  assert result["structuredContent"]["note"]["note_id"] == "trailing"


# ---- Drain 2026-07-23-1700 Phase 1 — sync_state exposure ----

_NOTE_WITH_SYNC_STATE = """---
type: action
inputs: []
recipe_version: 1
source_facet: description
sync_state: stale-recipe
---

# Description

Body with a description edit that hasn't been re-derived yet.

# Recipe

Return "prior".
"""

_NOTE_WITHOUT_SYNC_STATE = """---
type: action
inputs: []
recipe_version: 1
source_facet: description
---

# Description

Pre-drain-1700 note; the plugin hasn't seeded sync_state yet.

# Recipe

Return "hi".
"""


@pytest.mark.asyncio
async def test_read_note_returns_sync_state(
  single_vault_registry: VaultRegistry,
):
  """Drain 2026-08-17-0100 (Phase 2) — was: "sync_state present in
  frontmatter surfaces on NoteContent." It no longer surfaces; it is
  DERIVED. This fixture stores `stale-recipe` while carrying no hash
  stamps at all, so the honest answer is `unknown` — a claim with
  nothing behind it is exactly what the retirement was for."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "stale_desc", _NOTE_WITH_SYNC_STATE)
  result = await read_note.run(
    arguments={"note_id": "stale_desc"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  note = result["structuredContent"]["note"]
  assert note["sync_state"] == "unknown"


@pytest.mark.asyncio
async def test_read_note_missing_sync_state(
  single_vault_registry: VaultRegistry,
):
  """Drain 2026-08-17-0100 (Phase 2) — was: "absent → None; callers must
  treat None as 'unknown'." The instruction is now the value: absent
  lineage derives `unknown` and no caller has to remember the rule."""
  vault_fs = single_vault_registry.get()
  _write(vault_fs.root, "pre_drain", _NOTE_WITHOUT_SYNC_STATE)
  result = await read_note.run(
    arguments={"note_id": "pre_drain"},
    bearer="tok",
    vault_registry=single_vault_registry,
  )
  assert result["isError"] is False
  note = result["structuredContent"]["note"]
  assert note["sync_state"] == "unknown"


# ---------------------------------------------------------------------------
# undeclared_inputs_detected (drain 2026-08-13-0230, Option C from 2135).
# A FLAG, not a derivation: `inputs` still reports only what is declared.
# Wizard trusted `inputs: []` as "takes no parameters", omitted
# inputs=["bars"] on commit, and hit a runtime TypeError from a zero-arg
# function. This distinguishes "confidently none" from "none declared, but
# the body wants some".
# ---------------------------------------------------------------------------

_NOTE_NO_INPUTS_CLEAN = """---
type: action
---

# Description

doc.

# Recipe

Let greeting = "hi".
Return greeting.
"""

_NOTE_NO_INPUTS_BUT_BODY_WANTS = """---
type: action
---

# Description

doc.

# Recipe

Let kp = Call [[play_at_offsets]] with instrument=kick_i, bars=bars.
Return kp.
"""

_NOTE_INPUTS_DECLARED = """---
type: action
inputs: [bars]
---

# Description

doc.

# Recipe

Let kp = Call [[play_at_offsets]] with bars=bars.
Return kp.
"""


async def _read(registry, note_id, body):
  _write(registry.get().root, note_id, body)
  result = await read_note.run(
    arguments={"note_id": note_id}, bearer="tok", vault_registry=registry,
  )
  assert result["isError"] is False
  return result["structuredContent"]["note"]


@pytest.mark.asyncio
async def test_undeclared_inputs_not_flagged_when_body_is_self_contained(
  single_vault_registry: VaultRegistry,
):
  """(a) No inputs declared, no free identifiers → confidently none."""
  note = await _read(single_vault_registry, "clean", _NOTE_NO_INPUTS_CLEAN)
  assert note["inputs"] == []
  assert note["undeclared_inputs_detected"] is False
  assert note["undeclared_inputs_summary"] is None


@pytest.mark.asyncio
async def test_undeclared_inputs_flagged_when_body_references_free_name(
  single_vault_registry: VaultRegistry,
):
  """(b) No inputs declared, body references `bars` → flagged."""
  note = await _read(single_vault_registry, "wants", _NOTE_NO_INPUTS_BUT_BODY_WANTS)
  assert note["inputs"] == [], "the flag must NOT change what `inputs` reports"
  assert note["undeclared_inputs_detected"] is True
  assert "bars" in (note["undeclared_inputs_summary"] or "")
  assert "kick_i" in (note["undeclared_inputs_summary"] or "")


@pytest.mark.asyncio
async def test_undeclared_inputs_never_flagged_when_inputs_declared(
  single_vault_registry: VaultRegistry,
):
  """(c) inputs declared → never flagged, regardless of body."""
  note = await _read(single_vault_registry, "declared", _NOTE_INPUTS_DECLARED)
  assert note["inputs"] == ["bars"]
  assert note["undeclared_inputs_detected"] is False
  assert note["undeclared_inputs_summary"] is None
