"""Drift guard: forge-mcp's Description placeholder vs the plugin's.

Drain 2026-08-23-2100. Both authoring surfaces seed a fresh note's
`# Description` with the same hint, and the plugin's /generate path
recognises that exact string to keep the hint from reaching the LLM as
intent. A one-character divergence between the two copies fails OPEN:
MCP-created notes would carry a hint the plugin no longer recognises,
and the model would be asked to implement "Describe what this note
should do".

Separate repos, so no import. This reads the plugin's TypeScript source
and compares line-for-line — the mirror-drift rule's option (b), the
same shape as the engine-libs drift checks in forge-transpile.
"""

import re
from pathlib import Path

import pytest

from forge_mcp.description_placeholder import (
  DESCRIPTION_PLACEHOLDER,
  is_description_placeholder,
)

PLUGIN_CORE = (
  Path.home() / "projects" / "forge-client-obsidian"
  / "src" / "description-placeholder-core.ts"
)


def _plugin_lines() -> list[str]:
  src = PLUGIN_CORE.read_text(encoding="utf-8")
  start = src.index("export const DESCRIPTION_PLACEHOLDER = [")
  block = src[start:src.index("].join(", start)]
  return re.findall(r"^\s*'(.*)',$", block, re.MULTILINE)


@pytest.mark.skipif(
  not PLUGIN_CORE.exists(),
  reason=f"plugin sibling checkout not present at {PLUGIN_CORE}",
)
def test_placeholder_matches_the_plugin_line_for_line():
  assert _plugin_lines() == DESCRIPTION_PLACEHOLDER.split("\n")


@pytest.mark.skipif(
  not PLUGIN_CORE.exists(),
  reason=f"plugin sibling checkout not present at {PLUGIN_CORE}",
)
def test_the_extractor_actually_finds_lines():
  """Non-vacuity: an extractor that quietly returned [] would make the
  comparison above pass only when our own constant was empty too, and
  be green for the wrong reason the rest of the time."""
  assert len(_plugin_lines()) >= 2


def test_edited_placeholder_is_not_the_placeholder():
  """Non-vacuity for the recogniser: it must stop matching the moment
  an author writes over the hint, or MCP-authored intent would be
  discarded."""
  assert is_description_placeholder(DESCRIPTION_PLACEHOLDER)
  assert not is_description_placeholder(DESCRIPTION_PLACEHOLDER + " And scale it.")
  assert not is_description_placeholder("")
