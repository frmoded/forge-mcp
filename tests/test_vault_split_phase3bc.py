"""Vault-split 3b (registry import-roots) + 3c (walker wiring).

Drain 2026-08-05-1900. First production callers of drain 1710's
`parse_imports`/`detect_cycle` and drain 1430's `resolve_link` — the
registry parses `[imports]` at registration and rejects cycles; the
closure walker resolves wikilinks through registry-provided import
roots and collects resolution errors.

Namespaced links appear only in walker `source` strings, never in
fixture-note Recipes: the E-- grammar rejects `[[ns:note]]` today
(see tests/vault_fixtures.py header). Note Recipes here are bare-link
only, parse-verified per I14 at drain time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.vault_fs import VaultFS
from forge_mcp.vault_imports import VaultImportError, parse_imports
from forge_mcp.vault_note_closure import (
  ClosureResolutionError,
  build_vault_note_closure,
)
from forge_mcp.vault_registry import (
  VaultImportCycleError,
  VaultNotFoundError,
  VaultRegistry,
)
from tests.vault_fixtures import ACTION_NOTE, make_vault


def _registry_for(root: Path, name: str) -> VaultRegistry:
  return VaultRegistry({name: VaultFS(root=root)})


# ------------------------------------------------------------ registry (3b)


def test_registry_registers_import_root_from_forge_toml(fixture_vault_pair):
  reg = _registry_for(fixture_vault_pair["parent"], "parent")
  roots = reg.get_import_roots("parent")
  assert list(roots) == ["child"]
  assert roots["child"].root == fixture_vault_pair["child"].resolve()


def test_registry_get_import_root_returns_path(fixture_vault_pair):
  reg = _registry_for(fixture_vault_pair["parent"], "parent")
  fs = reg.get_import_root("parent", "child")
  assert fs.root == fixture_vault_pair["child"].resolve()
  with pytest.raises(VaultNotFoundError, match="declares no import"):
    reg.get_import_root("parent", "bogus")


def test_registry_import_roots_not_in_list_vaults(fixture_vault_pair):
  reg = _registry_for(fixture_vault_pair["parent"], "parent")
  assert reg.names() == ["parent"]
  assert [v["name"] for v in reg.list()] == ["parent"]


def test_registry_vault_without_imports_has_empty_roots(tmp_path):
  solo = make_vault(tmp_path / "solo", "solo")
  reg = _registry_for(solo, "solo")
  assert reg.get_import_roots("solo") == {}


def test_registry_rejects_cycle_at_registration(tmp_path):
  make_vault(tmp_path / "a", "a", imports={"b": "../b"})
  make_vault(tmp_path / "b", "b", imports={"a": "../a"})
  with pytest.raises(VaultImportCycleError, match="a → b → a"):
    _registry_for(tmp_path / "a", "a")


def test_registry_rejects_transitive_cycle(tmp_path):
  make_vault(tmp_path / "a", "a", imports={"b": "../b"})
  make_vault(tmp_path / "b", "b", imports={"c": "../c"})
  make_vault(tmp_path / "c", "c", imports={"a": "../a"})
  with pytest.raises(VaultImportCycleError, match="a → b → c → a"):
    _registry_for(tmp_path / "a", "a")


def test_registry_diamond_import_is_not_cycle(tmp_path):
  # a → b → d and a → c → d: shared dependency, NOT a cycle (drain
  # 1710 test convention).
  make_vault(tmp_path / "d", "d")
  make_vault(tmp_path / "b", "b", imports={"d": "../d"})
  make_vault(tmp_path / "c", "c", imports={"d": "../d"})
  make_vault(tmp_path / "a", "a", imports={"b": "../b", "c": "../c"})
  reg = _registry_for(tmp_path / "a", "a")
  assert sorted(reg.get_import_roots("a")) == ["b", "c"]


def test_registry_add_rolls_back_on_broken_imports(tmp_path):
  solo = make_vault(tmp_path / "solo", "solo")
  reg = _registry_for(solo, "solo")
  broken = make_vault(
    tmp_path / "broken", "broken", imports={"ghost": "../does-not-exist"}
  )
  with pytest.raises(VaultImportError):
    reg.add("broken", VaultFS(root=broken))
  # The half-registered vault must not linger in either mapping.
  assert reg.names() == ["solo"]
  with pytest.raises(VaultNotFoundError):
    reg.get_import_roots("broken")


# ------------------------------------------------------------- walker (3c)


def _fss(pair):
  return VaultFS(root=pair["parent"]), {"child": VaultFS(root=pair["child"])}


def test_walker_uses_registry_imports(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  errors: list[ClosureResolutionError] = []
  got = build_vault_note_closure(
    "Return Call [[consumer]].", parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  assert errors == []
  # consumer (parent) + shared_note (resolved through the import).
  assert [e["name"] for e in got] == ["shared_note", "consumer"]
  assert 'from child' in got[0]["recipe_source"]


def test_walker_resolves_local_first(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  # Give the parent its OWN shared_note; local must shadow the import.
  (fixture_vault_pair["parent"] / "shared_note.md").write_text(
    ACTION_NOTE.format(desc="Local twin.", recipe='Return "from parent".'),
    encoding="utf-8",
  )
  got = build_vault_note_closure(
    "Return Call [[shared_note]].", parent_fs,
    local_vault_name="parent", imports=imports,
  )
  assert len(got) == 1
  assert "from parent" in got[0]["recipe_source"]


def test_walker_resolves_namespaced_form(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  # Local twin present — the namespaced form must bypass it.
  (fixture_vault_pair["parent"] / "shared_note.md").write_text(
    ACTION_NOTE.format(desc="Local twin.", recipe='Return "from parent".'),
    encoding="utf-8",
  )
  errors: list[ClosureResolutionError] = []
  got = build_vault_note_closure(
    "Return Call [[child:shared_note]].", parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  assert errors == []
  assert len(got) == 1
  assert "from child" in got[0]["recipe_source"]


def test_walker_errors_on_missing_namespaced_target(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  errors: list[ClosureResolutionError] = []
  got = build_vault_note_closure(
    "Return Call [[child:nonexistent]].", parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  assert got == []
  assert len(errors) == 1
  assert errors[0].reason == "not-found-in-namespace"
  assert errors[0].wikilink == "child:nonexistent"
  assert errors[0].origin_note == "<entry recipe>"


def test_walker_errors_on_unknown_namespace(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  errors: list[ClosureResolutionError] = []
  build_vault_note_closure(
    "Return Call [[bogus:x]].", parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  assert len(errors) == 1
  assert errors[0].reason == "unknown-namespace"
  assert "bogus" in errors[0].message


def test_walker_falls_through_to_engine_lib(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  errors: list[ClosureResolutionError] = []
  got = build_vault_note_closure(
    "Return Call [[melodic_line]] with pattern=p, pitches=q.", parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  # Nothing local, nothing imported → engine's problem, not an error.
  assert got == []
  assert errors == []


def test_walker_collects_multiple_errors(fixture_vault_pair):
  parent_fs, imports = _fss(fixture_vault_pair)
  errors: list[ClosureResolutionError] = []
  build_vault_note_closure(
    "Let a = Call [[bogus:x]].\n"
    "Let b = Call [[child:missing_one]].\n"
    "Return Call [[child:missing_two]].",
    parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  assert len(errors) == 3
  assert [e.wikilink for e in errors] == [
    "bogus:x", "child:missing_one", "child:missing_two",
  ]


def test_walker_error_carries_origin_note_path(tmp_path):
  # The ambiguous-bare-link case doubles as the origin test: it is the
  # one error class expressible INSIDE an E---parseable Recipe (bare
  # link, two imports both defining it).
  make_vault(
    tmp_path / "left", "left",
    notes={"dup_note": ACTION_NOTE.format(desc="l", recipe='Return "l".')},
  )
  make_vault(
    tmp_path / "right", "right",
    notes={"dup_note": ACTION_NOTE.format(desc="r", recipe='Return "r".')},
  )
  parent = make_vault(
    tmp_path / "parent", "parent",
    imports={"left": "../left", "right": "../right"},
    notes={
      "consumer": ACTION_NOTE.format(
        desc="Ambiguous caller.", recipe="Return Call [[dup_note]].",
      ),
    },
  )
  parent_fs = VaultFS(root=parent)
  imports = {
    "left": VaultFS(root=tmp_path / "left"),
    "right": VaultFS(root=tmp_path / "right"),
  }
  errors: list[ClosureResolutionError] = []
  build_vault_note_closure(
    "Return Call [[consumer]].", parent_fs,
    local_vault_name="parent", imports=imports, errors=errors,
  )
  assert len(errors) == 1
  assert errors[0].reason == "ambiguous"
  assert errors[0].origin_note == "consumer"
  assert errors[0].message.startswith("consumer:")
  assert "left" in errors[0].message and "right" in errors[0].message


def test_walker_imported_note_resolves_bare_links_in_its_own_vault(tmp_path):
  # A note reached through an import walks in ITS vault: its bare
  # links resolve against the child tree even when the parent has a
  # same-named note.
  child = make_vault(
    tmp_path / "child", "child",
    notes={
      "entry_point": ACTION_NOTE.format(
        desc="Child entry.", recipe="Return Call [[helper]].",
      ),
      "helper": ACTION_NOTE.format(desc="h", recipe='Return "child helper".'),
    },
  )
  parent = make_vault(
    tmp_path / "parent", "parent",
    imports={"child": "../child"},
    notes={
      "helper": ACTION_NOTE.format(desc="h", recipe='Return "parent helper".'),
    },
  )
  got = build_vault_note_closure(
    "Return Call [[child:entry_point]].", VaultFS(root=parent),
    local_vault_name="parent", imports={"child": VaultFS(root=child)},
  )
  assert [e["name"] for e in got] == ["helper", "entry_point"]
  assert "child helper" in got[0]["recipe_source"]


def test_walker_without_imports_matches_prior_behavior(fixture_vault_pair):
  # No imports passed: bare links resolve locally or fall through, and
  # namespaced links (which pre-drain-1900 were silently skipped) now
  # surface as unknown-namespace — the one deliberate change.
  parent_fs = VaultFS(root=fixture_vault_pair["parent"])
  errors: list[ClosureResolutionError] = []
  got = build_vault_note_closure(
    "Return Call [[consumer]].", parent_fs, errors=errors,
  )
  assert [e["name"] for e in got] == ["consumer"]
  assert errors == []  # its [[shared_note]] falls through to engine


# ------------------------------------------------------------- harness


def test_fixture_harness_parses_forge_toml(fixture_vault_pair):
  decls = parse_imports(fixture_vault_pair["parent"] / "forge.toml")
  assert list(decls) == ["child"]
  assert decls["child"].root == fixture_vault_pair["child"].resolve()
