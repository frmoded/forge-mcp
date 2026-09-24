"""Drain 2026-09-24-2300 — structural guard for the silent-drop bug class.

Drain 2200 found `_forge_render_music` declaring `simultaneous` in its
signature (so the schema advertised it and calls were accepted) without ever
copying it into the `args` handed downstream. This test makes that shape a
failure for EVERY registered tool wrapper: each non-`ctx` parameter must be
referenced somewhere in the wrapper body. A parameter that is only ever
declared cannot possibly reach the tool implementation.

It is deliberately syntactic (AST, no network) so it stays cheap and covers all
wrappers at once; the behavioural, drive-the-registered-tool check for
`simultaneous` lives in test_render_music_simultaneous_wrapper.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "src" / "forge_mcp" / "server.py"


def _tool_wrappers(source: str) -> list[ast.AsyncFunctionDef]:
  tree = ast.parse(source)
  out = []
  for node in ast.walk(tree):
    if isinstance(node, ast.AsyncFunctionDef) and any(
      isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "tool"
      for d in node.decorator_list
    ):
      out.append(node)
  return out


def _unreferenced_params(fn: ast.AsyncFunctionDef) -> list[str]:
  params = [a.arg for a in fn.args.args if a.arg not in ("ctx", "self")]
  used = {
    n.id
    for stmt in fn.body
    for n in ast.walk(stmt)
    if isinstance(n, ast.Name)
  }
  return [p for p in params if p not in used]


def test_guard_is_not_vacuous_and_detects_a_dropped_param():
  wrappers = _tool_wrappers(SERVER.read_text())
  assert len(wrappers) >= 28, f"found only {len(wrappers)} tool wrappers"
  # Non-vacuity: the pre-fix render_music shape (declared, never used) is flagged.
  buggy = (
    "@server.tool(name='x')\n"
    "async def w(ctx, pitches, simultaneous=None):\n"
    "  args = {'pitches': pitches}\n"
    "  return args\n"
  )
  (fn,) = _tool_wrappers(buggy)
  assert _unreferenced_params(fn) == ["simultaneous"]


def test_every_tool_wrapper_references_every_declared_parameter():
  offenders = {
    fn.name: missing
    for fn in _tool_wrappers(SERVER.read_text())
    if (missing := _unreferenced_params(fn))
  }
  assert offenders == {}, (
    "these wrappers declare parameters they never use (silently dropped): "
    f"{offenders}"
  )
