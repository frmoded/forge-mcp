"""Tests for the forge-note:///{domain}/{name} URI parser."""
from __future__ import annotations

import pytest

from forge_mcp.resources.note_uri import build_forge_note_uri, parse_forge_note_uri


def test_valid_uri_extracts_domain_and_name() -> None:
  assert parse_forge_note_uri("forge-note:///music/compose_blues") == (
    "music",
    "compose_blues",
  )


def test_valid_uri_with_moda_domain() -> None:
  assert parse_forge_note_uri("forge-note:///moda/kick") == ("moda", "kick")


def test_rejects_wrong_scheme() -> None:
  with pytest.raises(ValueError, match="Invalid scheme"):
    parse_forge_note_uri("forge-recipe:///music/compose_blues")


def test_rejects_missing_name_segment() -> None:
  with pytest.raises(ValueError):
    parse_forge_note_uri("forge-note:///music")


def test_rejects_extra_path_segments() -> None:
  with pytest.raises(ValueError, match="exactly two path segments"):
    parse_forge_note_uri("forge-note:///music/compose_blues/v1")


def test_rejects_empty_domain() -> None:
  with pytest.raises(ValueError):
    parse_forge_note_uri("forge-note:////compose_blues")


def test_rejects_empty_name() -> None:
  with pytest.raises(ValueError):
    parse_forge_note_uri("forge-note:///music/")


def test_rejects_surrounding_whitespace() -> None:
  with pytest.raises(ValueError, match="whitespace"):
    parse_forge_note_uri("  forge-note:///music/compose_blues  ")


def test_rejects_authority_component() -> None:
  # Double-slash instead of triple — has authority "music" and path "/x"
  with pytest.raises(ValueError):
    parse_forge_note_uri("forge-note://music/compose_blues")


def test_rejects_query_string() -> None:
  with pytest.raises(ValueError, match="query or fragment"):
    parse_forge_note_uri("forge-note:///music/compose_blues?v=1")


def test_build_uri_roundtrips_through_parser() -> None:
  uri = build_forge_note_uri("music", "walking_bass")
  assert parse_forge_note_uri(uri) == ("music", "walking_bass")
