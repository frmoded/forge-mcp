"""Drain 2026-08-14-2200 — the two render-viz kind lists must not drift.

`forge_render_viz`'s `_ALLOWED_KINDS` and forge-transpile's `viz.VIZ_KINDS` are
hand-synced across two repos with no shared import. The comment at
`render_viz.py:51` has named that as a known hazard since drain `1100`, and each
new kind raises the odds of a silent disagreement — forge-mcp advertising a kind
forge-transpile rejects (a 400 the caller can't predict from the schema), or
refusing one it supports (a capability nobody can reach).

This test does not remove the duplication; the shared-module refactor is
explicitly out of scope. It makes the duplication *loud*.

WHY AST RATHER THAN IMPORT: I verified empirically that `viz.py` imports cleanly
in forge-mcp's environment today — its only top-level imports are `builtins`,
`math` and `typing`. But that is a property of the file right now, not a
guarantee. The moment someone adds a module-level `import music21` (or FastAPI,
or numpy) this test would start erroring in forge-mcp's env for a reason that has
nothing to do with kind-list drift. Parsing the literal out of the AST executes
nothing, so it cannot break that way — and §8 asks not to pull in
forge-transpile's dependency surface for this check.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from forge_mcp.tools import render_viz


def _sibling_repo() -> Path:
  """Locate forge-transpile.

  Mirrors the `${ENGINE_REPO:-../forge}` convention the existing cross-repo
  drift checks use — an env override with a sibling-directory default — but
  anchors the default to THIS FILE rather than the process cwd, so the test
  gives the same answer no matter where pytest was invoked from.
  """
  override = os.environ.get("FORGE_TRANSPILE_REPO")
  if override:
    return Path(override)
  return Path(__file__).resolve().parents[2] / "forge-transpile"


def _viz_kinds_via_ast(viz_py: Path) -> set[str]:
  """Extract the VIZ_KINDS tuple literal without executing the module."""
  tree = ast.parse(viz_py.read_text(), filename=str(viz_py))
  for node in tree.body:
    if not isinstance(node, ast.Assign):
      continue
    for target in node.targets:
      if isinstance(target, ast.Name) and target.id == "VIZ_KINDS":
        value = ast.literal_eval(node.value)
        return set(value)
  raise AssertionError(f"no top-level VIZ_KINDS assignment found in {viz_py}")


def test_kind_lists_are_in_sync():
  """Set equality — order is irrelevant, membership is not."""
  repo = _sibling_repo()
  viz_py = repo / "viz.py"
  if not viz_py.is_file():
    pytest.skip(f"forge-transpile sibling repo not present at {repo}")

  upstream = _viz_kinds_via_ast(viz_py)
  local = set(render_viz._ALLOWED_KINDS)

  missing_here = upstream - local
  extra_here = local - upstream
  assert not missing_here and not extra_here, (
    "render_viz kind lists have drifted.\n"
    f"  in viz.VIZ_KINDS but not in _ALLOWED_KINDS: {sorted(missing_here)}\n"
    f"  in _ALLOWED_KINDS but not in viz.VIZ_KINDS: {sorted(extra_here)}\n"
    "Both are hand-maintained; update whichever is behind "
    "(forge-transpile/viz.py, forge-mcp/src/forge_mcp/tools/render_viz.py)."
  )


def test_advertised_enum_matches_the_allowed_tuple():
  """The schema enum is what callers actually see, so check it too — a kind
  present in the tuple but absent from the enum is unreachable."""
  enum = set(render_viz.INPUT_SCHEMA["properties"]["kind"]["enum"])
  assert enum == set(render_viz._ALLOWED_KINDS)


def test_skips_cleanly_when_the_sibling_is_absent(monkeypatch):
  """§8 — anyone with only forge-mcp cloned must not get a failure. Exercised,
  not merely asserted in prose: point the lookup at a path that cannot exist
  and confirm the test function raises Skipped rather than failing."""
  monkeypatch.setenv("FORGE_TRANSPILE_REPO", "/nonexistent/forge-transpile-xyz")
  with pytest.raises(pytest.skip.Exception):
    test_kind_lists_are_in_sync()


def test_ast_extraction_finds_the_real_tuple():
  """Guard the extractor itself — if VIZ_KINDS were renamed or restructured,
  this test must fail loudly rather than the parity test silently comparing
  against an empty set."""
  repo = _sibling_repo()
  viz_py = repo / "viz.py"
  if not viz_py.is_file():
    pytest.skip("forge-transpile sibling repo not present")
  kinds = _viz_kinds_via_ast(viz_py)
  assert len(kinds) >= 6, kinds
  assert all(isinstance(k, str) and k for k in kinds)


def test_extractor_raises_when_the_assignment_is_gone(tmp_path):
  """The extractor must never return an empty set for a file that has no
  VIZ_KINDS — that would make the parity test vacuously comparable."""
  fake = tmp_path / "viz.py"
  fake.write_text("SOMETHING_ELSE = ('a', 'b')\n")
  with pytest.raises(AssertionError):
    _viz_kinds_via_ast(fake)


# --------------------------------------------- DESCRIPTION drift (drain 1110)


def test_description_names_every_allowed_kind():
  """Drain 2026-08-16-1110 — the tool DESCRIPTION is the discovery surface.

  An MCP-only agent cannot read the source; the description string IS how it
  learns which kinds exist. Wizard hand-authored nice_wave.svg / ugly_wave.svg
  via forge_create_asset because the description didn't mention
  sinewave_comparison or loudness_comparison — real work wasted to a stale
  string. Containment is enough here: the point is drift-proofing, not prose.
  """
  from forge_mcp.tools.render_viz import DESCRIPTION, _ALLOWED_KINDS

  missing = [k for k in _ALLOWED_KINDS if k not in DESCRIPTION]
  assert not missing, (
    f"forge_render_viz DESCRIPTION does not name: {missing}. "
    f"An agent that can only read the description cannot discover them."
  )


def test_schema_enum_description_names_every_allowed_kind():
  """Same guarantee for the per-kind guidance the schema carries."""
  from forge_mcp.tools.render_viz import INPUT_SCHEMA, _ALLOWED_KINDS

  text = INPUT_SCHEMA["properties"]["kind"]["description"]
  missing = [k for k in _ALLOWED_KINDS if k not in text]
  assert not missing, f"kind schema description does not name: {missing}"


def test_description_kind_check_is_not_vacuous():
  """The check above must fail when a kind is absent — otherwise it only
  proves the string is non-empty."""
  from forge_mcp.tools.render_viz import _ALLOWED_KINDS

  pretend_description = "Render a pedagogical diagram (sinewave) to an SVG."
  missing = [k for k in _ALLOWED_KINDS if k not in pretend_description]
  assert missing, "a description naming one kind must register the rest as missing"
