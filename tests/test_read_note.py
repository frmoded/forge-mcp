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
