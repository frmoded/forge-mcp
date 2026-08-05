"""Fixture-vault test harness. Drain 2026-08-05-1900 (vault-split 3b+3c).

Materializes toy vaults in `tmp_path` with real `forge.toml` manifests
and (optionally) `[imports]` links between them, so registry + walker
tests exercise the production parse/resolve path without touching real
vault directories. Reusable across future Phase 3+/4 drains — import
`make_vault` for bespoke topologies, or the `fixture_vault_pair`
fixture for the canonical parent-imports-child pair.

Import-declaration syntax note (step-1 finding): the accepted TOML
shape is `child = { local = "../child-vault" }`. The drain prompt
sketched a bare string (`child = "../child-vault"`), which
`vault_imports._parse_one` rejects with "must be a table" — the same
class of prompt-sketch drift `_parse_one` already guards against for
the `path` key.

Recipe bodies below are BARE-wikilink only and were parse-verified
against `forge.recipe.parser.parse` at drain time (I14). Namespaced
links (`[[child:note]]`) are deliberately absent from note Recipes:
the E-- grammar rejects them today (ParseError: "expected wikilink
after Call"), so tests exercise the namespaced form via the walker's
`source` parameter, which is wikilink-extracted but never E---parsed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ACTION_NOTE = (
  "---\n"
  "type: action\n"
  "recipe_version: 1\n"
  "---\n\n"
  "# Description\n\n{desc}\n\n"
  "# Recipe\n\n{recipe}\n"
)


def make_vault(
  root: Path,
  name: str,
  *,
  imports: dict[str, str] | None = None,
  notes: dict[str, str] | None = None,
) -> Path:
  """Materialize one toy vault: forge.toml (+[imports]) + notes.

  `imports` maps import-name -> local path (relative paths resolve
  against this vault, per the production parser). `notes` maps
  note_id -> full file body (use ACTION_NOTE.format(...) for action
  notes).
  """
  root.mkdir(parents=True, exist_ok=True)
  manifest = f'name = "{name}"\nversion = "0.1.0"\ndomains = ["test"]\n'
  if imports:
    manifest += "\n[imports]\n"
    for import_name, local in imports.items():
      manifest += f'{import_name} = {{ local = "{local}" }}\n'
  (root / "forge.toml").write_text(manifest, encoding="utf-8")
  for note_id, body in (notes or {}).items():
    path = root / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
  return root


@pytest.fixture
def fixture_vault_pair(tmp_path: Path) -> dict[str, Path]:
  """Two toy vaults; parent's forge.toml `[imports]` declares child.

  child holds `shared_note`; parent holds `consumer`, whose Recipe
  calls `[[shared_note]]` (bare — resolves through the import since
  parent has no local copy).
  """
  child = make_vault(
    tmp_path / "child-vault",
    "child",
    notes={
      "shared_note": ACTION_NOTE.format(
        desc="A note in the child vault.",
        recipe='Return "from child".',
      ),
    },
  )
  parent = make_vault(
    tmp_path / "parent-vault",
    "parent",
    imports={"child": "../child-vault"},
    notes={
      "consumer": ACTION_NOTE.format(
        desc="Calls a note that lives in the child vault.",
        recipe="Return Call [[shared_note]].",
      ),
    },
  )
  return {"parent": parent, "child": child}
