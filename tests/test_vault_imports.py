"""`forge.toml [imports]` parsing (drain 2026-08-05-0710, vault-split Phase 2).

Phase 2 is local-path only. These tests cover what can be validated
without a network: shape, name agreement, reserved names, path
resolution, and cycle reporting.

The rejection cases carry the weight. An import declaration is
configuration a human wrote by hand, and every one of these failures
would otherwise surface much later as a wikilink that mysteriously does
not resolve — at which point the manifest is the last place anyone
looks.
"""
from __future__ import annotations

import pytest

from forge_mcp.vault_imports import (
  VaultImportError,
  detect_cycle,
  parse_imports,
)


def _vault(tmp_path, name, *, imports_block=""):
  root = tmp_path / name
  root.mkdir(parents=True, exist_ok=True)
  (root / "forge.toml").write_text(
    f'name = "{name}"\nversion = "0.1.0"\ndomains = []\n{imports_block}',
    encoding="utf-8",
  )
  return root


class TestHappyPath:
  def test_no_imports_section_is_not_an_error(self, tmp_path):
    v = _vault(tmp_path, "solo")
    assert parse_imports(v / "forge.toml") == {}

  def test_relative_local_path_resolves_against_the_importing_vault(self, tmp_path):
    _vault(tmp_path, "music-core")
    v = _vault(tmp_path, "music-theory", imports_block='''
[imports]
music-core = { local = "../music-core" }
''')
    decls = parse_imports(v / "forge.toml")
    assert set(decls) == {"music-core"}
    assert decls["music-core"].root == (tmp_path / "music-core").resolve()

  def test_sha_and_tag_are_carried_but_unused(self, tmp_path):
    # Phase 2b needs them; recording now means a manifest written today
    # does not have to be rewritten then.
    _vault(tmp_path, "music-core")
    v = _vault(tmp_path, "music-theory", imports_block='''
[imports]
music-core = { local = "../music-core", git = "https://x/y.git", sha = "abc12345", tag = "v0.3.0" }
''')
    d = parse_imports(v / "forge.toml")["music-core"]
    assert (d.sha, d.tag, d.git) == ("abc12345", "v0.3.0", "https://x/y.git")


class TestRejections:
  def test_name_disagreement_is_an_error(self, tmp_path):
    # The manifest and the thing it points at disagree about what the
    # thing IS. Every later message naming the import would be lying.
    _vault(tmp_path, "actually-something-else")
    v = _vault(tmp_path, "importer", imports_block='''
[imports]
music-core = { local = "../actually-something-else" }
''')
    with pytest.raises(VaultImportError, match="name = 'actually-something-else'"):
      parse_imports(v / "forge.toml")

  def test_local_is_a_reserved_import_name(self, tmp_path):
    v = _vault(tmp_path, "importer", imports_block='''
[imports]
local = { local = "../whatever" }
''')
    with pytest.raises(VaultImportError, match="reserved name"):
      parse_imports(v / "forge.toml")

  def test_path_key_is_rejected_by_name(self, tmp_path):
    # The drain prompt for this phase used `path`; the spec says
    # `local`. Naming the right key beats "unknown field".
    v = _vault(tmp_path, "importer", imports_block='''
[imports]
music-core = { path = "../music-core" }
''')
    with pytest.raises(VaultImportError, match="The key is `local`"):
      parse_imports(v / "forge.toml")

  def test_git_without_local_says_phase_2_does_not_fetch(self, tmp_path):
    v = _vault(tmp_path, "importer", imports_block='''
[imports]
music-core = { git = "https://x/y.git", sha = "abc12345" }
''')
    with pytest.raises(VaultImportError, match="does not fetch remote imports"):
      parse_imports(v / "forge.toml")

  def test_missing_directory_names_what_it_resolved_against(self, tmp_path):
    v = _vault(tmp_path, "importer", imports_block='''
[imports]
music-core = { local = "../nope" }
''')
    with pytest.raises(VaultImportError, match="Relative paths resolve against"):
      parse_imports(v / "forge.toml")

  def test_directory_without_a_manifest_is_not_a_vault(self, tmp_path):
    (tmp_path / "music-core").mkdir()
    v = _vault(tmp_path, "importer", imports_block='''
[imports]
music-core = { local = "../music-core" }
''')
    with pytest.raises(VaultImportError, match="no forge.toml"):
      parse_imports(v / "forge.toml")

  def test_toml_error_mentions_the_imports_last_constraint(self, tmp_path):
    # Drain 1500's finding: [imports] above the flat keys swallows them.
    # The parse error is where someone will actually be looking.
    root = tmp_path / "broken"
    root.mkdir()
    (root / "forge.toml").write_text('name = "x"\n[[[bad', encoding="utf-8")
    with pytest.raises(VaultImportError, match="LAST section in the file"):
      parse_imports(root / "forge.toml")


class TestCycleDetection:
  def test_direct_cycle_reports_the_whole_path(self):
    cycle = detect_cycle("a", {"a": ["b"], "b": ["a"]})
    assert cycle == ["a", "b", "a"]

  def test_longer_cycle_reports_every_hop(self):
    # "a imports b imports c imports a" is actionable;
    # "cycle detected" is not.
    assert detect_cycle("a", {"a": ["b"], "b": ["c"], "c": ["a"]}) == ["a", "b", "c", "a"]

  def test_no_cycle_returns_none(self):
    assert detect_cycle("a", {"a": ["b"], "b": ["c"]}) is None

  def test_diamond_is_not_a_cycle(self):
    # a→b→d and a→c→d share d without cycling. A visited-set that
    # forgot to distinguish "on the current path" from "seen at all"
    # would call this a cycle.
    assert detect_cycle("a", {"a": ["b", "c"], "b": ["d"], "c": ["d"]}) is None
