"""Drain (inbox 2026-09-26-0300) — advertise the `title` / `subtitle` params.

forge-transpile's viz.py (b33d2ea) gave every kind optional `title` and
`subtitle` strings (the style guide puts a title + takeaway subtitle at the
bottom of every diagram). forge_render_viz forwards `params` untouched, so the
feature works — but an MCP-only agent cannot read the source, so a param that is
not in the tool's own text does not exist for it (same lesson as drain
2026-08-16-1110 for kinds). Pin that the schema names them.
"""
from forge_mcp.tools import render_viz


def test_params_schema_describes_title_and_subtitle():
  desc = render_viz.INPUT_SCHEMA["properties"]["params"]["description"]
  assert "title" in desc and "subtitle" in desc


def test_tool_description_mentions_title_and_subtitle():
  d = render_viz.DESCRIPTION.lower()
  assert "title" in d and "subtitle" in d
