"""Drain 2026-08-02-1815 — `facet` must survive the FastMCP wrapper.

Drain 1350 added `facet` to `edit_markdown_note.py`'s INPUT_SCHEMA and
to `run()`, and its tests + live smoke called `run()` directly. That
skipped the layer that actually broke: FastMCP derives each tool's
advertised inputSchema from the WRAPPER FUNCTION'S SIGNATURE in
server.py, not from the tool module's INPUT_SCHEMA dict. The wrapper
had no `facet` param, so MCP clients stripped the argument before it
left the client and every action-note edit was refused as though
`facet` had defaulted to "body".

These tests run at the tool-manager layer (the same layer
test_tool_wire_shape.py uses), which is where the strip happened.

`test_advertised_schema_declares_facet` is the one that fails against
the pre-drain wrapper — it reads the schema FastMCP actually publishes.
"""

from __future__ import annotations

import subprocess

import pytest
from mcp.types import CallToolResult

from forge_mcp.server import _make_server

ACTION_NOTE = """---
type: action
source_facet: recipe
sync_state: synced
---

# Description

See [[exercises/complete_this_scale_challenge]].

# Recipe

Call play_scale with tonic = "C"

# Python

def run():
    return play_scale("C")
"""


class _FakeReqCtx:
  request = None


class _FakeCtx:
  request_context = _FakeReqCtx()


def _tool(server, name):
  return server._tool_manager.get_tool(name)


@pytest.fixture
def vault_server(tmp_path, monkeypatch):
  """Server whose registry points at a git-tracked scratch vault.

  The VaultRegistry snapshots env at construction, so setenv must
  precede _make_server() (same constraint noted in
  test_tool_wire_shape.py's read_notes_in_vault test).
  """
  vault = (tmp_path / "facet-wire-vault").resolve()
  vault.mkdir()
  subprocess.run(["git", "init", "-q"], cwd=vault, check=True)
  subprocess.run(
    ["git", "config", "user.email", "t@example.com"], cwd=vault, check=True,
  )
  subprocess.run(["git", "config", "user.name", "T"], cwd=vault, check=True)
  (vault / "act.md").write_text(ACTION_NOTE, encoding="utf-8")
  (vault / "plain.md").write_text("vanilla prose\n", encoding="utf-8")
  subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
  subprocess.run(["git", "commit", "-qm", "seed"], cwd=vault, check=True)

  monkeypatch.setenv("FORGE_VAULT_PATH", str(vault))
  monkeypatch.setenv("FORGE_MCP_BEARER", "test-token-facet-wire")
  return vault, _make_server()


def test_advertised_schema_declares_facet(vault_server):
  """The regression test proper — reads the schema FastMCP publishes.

  Fails against the pre-drain wrapper: `facet` was in the tool module's
  INPUT_SCHEMA but absent from the wrapper signature, and it is the
  wrapper signature that FastMCP turns into the wire schema.
  """
  _vault, server = vault_server
  schema = _tool(server, "forge_edit_markdown_note").parameters
  props = schema["properties"]

  assert "facet" in props, (
    "forge_edit_markdown_note's advertised inputSchema must declare "
    f"`facet`; got {sorted(props)}. FastMCP builds this from the "
    "wrapper signature in server.py — updating INPUT_SCHEMA alone is "
    "not enough."
  )
  # Optional with a default, so pre-drain callers keep working.
  assert "facet" not in schema.get("required", [])

  # The allowed values must reach the client too. A bare `facet: str`
  # annotation publishes `{"type": "string"}` and the caller has to
  # guess "description" from prose — the same class of problem this
  # drain fixes, one level down. `Literal[...]` in the wrapper signature
  # is what puts the enum on the wire.
  allowed = props["facet"].get("enum") or props["facet"].get("const")
  assert allowed == ["body", "description"], (
    f"advertised facet schema should enumerate the allowed values; got "
    f"{props['facet']}"
  )


@pytest.mark.asyncio
async def test_facet_description_reaches_handler_through_wrapper(vault_server):
  """End-to-end through the tool manager: the Description rewrite fires."""
  vault, server = vault_server
  tool = _tool(server, "forge_edit_markdown_note")

  result = await tool.run(
    arguments={
      "note_id": "act",
      "body": "See [[music_theory/exercises/complete_this_scale_challenge]].",
      "facet": "description",
    },
    context=_FakeCtx(),
    convert_result=True,
  )

  assert isinstance(result, CallToolResult)
  assert result.isError is not True, result.content

  after = (vault / "act.md").read_text(encoding="utf-8")
  assert "[[music_theory/exercises/complete_this_scale_challenge]]" in after
  assert "[[exercises/complete_this_scale_challenge]]" not in after
  # Only the Description moved.
  assert 'Call play_scale with tonic = "C"' in after
  assert 'return play_scale("C")' in after

  assert result.structuredContent is not None
  assert result.structuredContent["git_sha"], "auto-commit SHA expected"

  subject = subprocess.run(
    ["git", "log", "-1", "--format=%s"],
    cwd=vault, check=True, capture_output=True, text=True,
  ).stdout.strip()
  assert subject == "forge_edit_markdown_note (facet=description): act"


@pytest.mark.asyncio
async def test_omitting_facet_still_defaults_to_body(vault_server):
  """Pre-drain call shape — no `facet` key — keeps working on vanilla."""
  vault, server = vault_server
  tool = _tool(server, "forge_edit_markdown_note")

  result = await tool.run(
    arguments={"note_id": "plain", "body": "rewritten\n"},
    context=_FakeCtx(),
    convert_result=True,
  )

  assert result.isError is not True, result.content
  assert (vault / "plain.md").read_text(encoding="utf-8") == "rewritten\n"


@pytest.mark.asyncio
async def test_action_note_without_facet_is_still_refused(vault_server):
  """The refusal wizard hit — must persist for a genuine facet-less call."""
  vault, server = vault_server
  tool = _tool(server, "forge_edit_markdown_note")

  result = await tool.run(
    arguments={"note_id": "act", "body": "clobber"},
    context=_FakeCtx(),
    convert_result=True,
  )

  assert result.isError is True
  assert (vault / "act.md").read_text(encoding="utf-8") == ACTION_NOTE
