"""Schema parity for the foreign-changes fields (drain 2026-08-05-0830).

Drain 2026-08-03-1540 added `foreign_changes_detected` /
`foreign_changes_summary` to the five tools that call `auto_commit`.
`delete_note` and `rename_note` commit through `_git_commit_paths`
instead, so they were left out — correct on the implementation axis,
and a discipline burden on wizard, whose HARD RULE 3b now says to check
the field on every git-touching tool.

These tests pin the parity set rather than the individual tools: the
failure this guards against is a NEW git-touching tool shipping without
the fields, which no per-tool test would catch.

`commit_recipe` is the single deliberate exception. Asserting its
ABSENCE is the point — drain 1540 kept its bare-SHA contract in scope
as a known gap, and a future drain that "fixes parity" by extending it
should have to delete a test that says not to.
"""
from __future__ import annotations

import pytest

from forge_mcp import schemas

FOREIGN_FIELDS = ("foreign_changes_detected", "foreign_changes_summary")

# Every git-touching tool's result model EXCEPT commit_recipe.
PARITY_MODELS = [
  schemas.CreateMarkdownNoteResult,
  schemas.EditMarkdownNoteResult,
  schemas.DeleteNoteResult,
  schemas.RenameNoteResult,
]


@pytest.mark.parametrize("model", PARITY_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("field", FOREIGN_FIELDS)
def test_git_touching_models_declare_the_field(model, field):
  assert field in model.model_fields, (
    f"{model.__name__} is git-touching but lacks {field!r}. Wizard's "
    "HARD RULE 3b reads this field on every such tool; a missing one "
    "silently reads as 'no foreign change' when it means 'no answer'."
  )


@pytest.mark.parametrize("model", PARITY_MODELS, ids=lambda m: m.__name__)
def test_defaults_are_false_and_none(model):
  # Constructed with no foreign-change info at all — the shape a tool
  # that never detects will always produce.
  fields = model.model_fields
  assert fields["foreign_changes_detected"].default is False
  assert fields["foreign_changes_summary"].default is None


def test_commit_recipe_is_the_single_documented_exception():
  """Deliberate. See drain 1540 §Not-in-scope and this drain's §Don'ts.

  If a future drain extends CommitResult, it should delete this test on
  purpose and say why — not discover the gap by surprise.
  """
  for field in FOREIGN_FIELDS:
    assert field not in schemas.CommitResult.model_fields


def test_delete_and_rename_report_not_checked_not_clean():
  """The fields are present, and they mean something weaker here.

  `delete_note` / `rename_note` never compare anything: a deleted file
  has no post-write content, and a moved one has no stable path to
  compare on. False from these tools means NOT CHECKED. The docstrings
  say so; this test pins that the docstrings say so, because the value
  alone cannot distinguish the two cases.
  """
  for model in (schemas.DeleteNoteResult, schemas.RenameNoteResult):
    desc = model.model_fields["foreign_changes_detected"].description or ""
    assert "ALWAYS False" in desc
    assert "_git_commit_paths" in desc
    assert "NOT CHECKED" in desc
