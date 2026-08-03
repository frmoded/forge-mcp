"""Drain 2026-08-03-1110 — wrapper signatures must cover their INPUT_SCHEMA.

FastMCP builds each tool's client-facing inputSchema from the WRAPPER
FUNCTION'S SIGNATURE in server.py, not from the tool module's
`INPUT_SCHEMA` dict. A parameter declared only in `INPUT_SCHEMA` never
reaches the wire: the client strips it before sending, the handler sees
its default, and every handler-level unit test still passes. The failure
surfaces only when a real MCP client calls the tool.

That has now shipped twice:
  * drain 1405 — `resolve_slot` on forge_run_recipe (fixed by drain 1200)
  * drain 1350 — `facet` on forge_edit_markdown_note (fixed by drain 1815)

Both were found by a human hitting the broken tool, not by CI. This test
is the CI half. It fails loudly with a table when a wrapper drifts.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from forge_mcp.server import _make_server

# Tools with no `tools/<name>.py` INPUT_SCHEMA to compare against.
# Listed explicitly rather than skipped silently — a NEW tool that
# forgets its INPUT_SCHEMA must trip `test_exempt_list_is_current`
# instead of quietly opting out of parity checking.
_NO_SCHEMA_EXEMPT = {
  # Handler lives inline in server.py; no tools/ module.
  "forge_mark_message_read",
  # Module exists but declares no INPUT_SCHEMA.
  "forge_read_messages",
}


@pytest.fixture(scope="module")
def server(tmp_path_factory, ):
  import os

  vault = tmp_path_factory.mktemp("parity-vault")
  os.environ["FORGE_VAULT_PATH"] = str(vault)
  os.environ.setdefault("FORGE_MCP_BEARER", "test-token-parity")
  return _make_server()


def _schema_keys(tool_name: str) -> list[str] | None:
  """INPUT_SCHEMA property keys for a tool, or None if it has none."""
  try:
    mod = importlib.import_module(
      f"forge_mcp.tools.{tool_name.removeprefix('forge_')}"
    )
  except ModuleNotFoundError:
    return None
  schema = getattr(mod, "INPUT_SCHEMA", None)
  if schema is None:
    return None
  return list(schema.get("properties", {}))


def _wrapper_params(tool) -> list[str]:
  """Wrapper signature params, minus the injected `ctx`."""
  return [p for p in inspect.signature(tool.fn).parameters if p != "ctx"]


def _covers(schema_key: str, wrapper: list[str]) -> bool:
  """Does the wrapper expose `schema_key`?

  Python reserved words can't be parameter names, so a schema key like
  `from` is spelled `from_` in the wrapper and mapped back when the
  args dict is built (see _forge_write_message). A trailing underscore
  is therefore an accepted spelling, not drift.
  """
  return schema_key in wrapper or f"{schema_key}_" in wrapper


def test_every_wrapper_covers_its_input_schema(server):
  """The regression guard. Fails with a table naming each drift."""
  drifts: list[str] = []

  for tool in sorted(server._tool_manager.list_tools(), key=lambda t: t.name):
    keys = _schema_keys(tool.name)
    if keys is None:
      continue  # covered by test_exempt_list_is_current
    wrapper = _wrapper_params(tool)
    missing = [k for k in keys if not _covers(k, wrapper)]
    if missing:
      drifts.append(
        f"  {tool.name}\n"
        f"    missing from wrapper: {missing}\n"
        f"    wrapper params:       {wrapper}\n"
        f"    INPUT_SCHEMA keys:    {keys}"
      )

  assert not drifts, (
    "FastMCP derives each tool's wire schema from the wrapper signature "
    "in server.py, so a param declared only in INPUT_SCHEMA is silently "
    "stripped client-side and never reaches run().\n\n"
    "Add the parameter to the wrapper's signature AND to the args dict "
    "it passes to run() — see _forge_edit_markdown_note's `facet` for "
    "the pattern.\n\n" + "\n".join(drifts)
  )


def test_exempt_list_is_current(server):
  """The exempt list must name exactly the schema-less tools.

  Without this, a new tool shipped without an INPUT_SCHEMA would be
  skipped by the parity test and inherit the very blind spot this file
  exists to close.
  """
  actual = {
    tool.name
    for tool in server._tool_manager.list_tools()
    if _schema_keys(tool.name) is None
  }
  assert actual == _NO_SCHEMA_EXEMPT, (
    f"schema-less tools changed.\n"
    f"  now schema-less but not exempt: {sorted(actual - _NO_SCHEMA_EXEMPT)}\n"
    f"  exempt but now has a schema:    {sorted(_NO_SCHEMA_EXEMPT - actual)}\n"
    "If a tool gained an INPUT_SCHEMA, drop it from _NO_SCHEMA_EXEMPT so "
    "parity is enforced. If a NEW tool has no INPUT_SCHEMA, prefer giving "
    "it one over adding it here."
  )


def test_parity_check_would_catch_a_planted_drift(server):
  """The guard must actually fail on drift — a green test that can never
  go red is worth nothing. Plants a schema key no wrapper declares and
  asserts the same predicate the real test uses rejects it."""
  tool = server._tool_manager.get_tool("forge_edit_markdown_note")
  wrapper = _wrapper_params(tool)

  assert _covers("facet", wrapper), "sanity: facet is really wired"
  assert not _covers("definitely_not_a_param", wrapper)
