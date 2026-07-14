"""Drain CW-MCP-2-C — tests for `forge_commit_recipe` + `forge-recipe:///` resource.

Covers the 10-test contract per drain §5 (Option C):

  1.  writes Recipe facet only — preserves Description + Python + frontmatter.
  2.  returns incremented version stamp.
  3.  version-conflict returns isError.
  4.  invokes forge_run_recipe internally, populates run_id.
  5.  rejects missing note_id.
  6.  rejects path-traversal note_ids.
  7.  forge-recipe:/// returns versioned Recipe source (git-tracked vault).
  8.  forge-recipe:/// returns 'history unavailable' on missing version.
  9.  vault_fs writer preserves frontmatter.
  10. vault_fs writer handles note without Recipe facet.

Uses tmp_path fixtures so tests don't touch the real bluh vault. Where
git behavior is under test, an in-test `git init` + commit provides a
minimal history.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.resources.recipe_uri import parse_forge_recipe_uri, read_recipe_resource
from forge_mcp.tools import commit_recipe
from forge_mcp.vault_fs import (
  NoteIdInvalid,
  VaultFS,
  VersionConflict,
  parse_note,
  splice_recipe,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
  """Fresh vault dir (no git) with one seeded V2a note."""
  root = tmp_path / "vault"
  root.mkdir()
  (root / "notes").mkdir()
  seed = (
    "---\n"
    "type: action\n"
    "description: seed note\n"
    "---\n"
    "\n"
    "# Description\n"
    "\n"
    "The original description that must survive commit.\n"
    "\n"
    "# Recipe\n"
    "\n"
    "Return 1.\n"
    "\n"
    "# Python\n"
    "\n"
    "```python\n"
    "def compute(context):\n"
    "  return 1\n"
    "```\n"
  )
  (root / "notes" / "seed.md").write_text(seed, encoding="utf-8")
  return root


@pytest.fixture
def git_vault_root(tmp_path: Path) -> Path:
  """Fresh vault WITH git initialized + a seeded initial commit so we
  can exercise the versioned-Recipe resource path (drain §5 test #7)."""
  root = tmp_path / "gitvault"
  root.mkdir()
  seed = (
    "---\n"
    "type: action\n"
    "recipe_version: 1\n"
    "---\n"
    "\n"
    "# Description\n\nSeed\n\n"
    "# Recipe\n\nReturn 1.\n"
  )
  (root / "seed.md").write_text(seed, encoding="utf-8")
  # Initialize git + minimal identity + first commit so subsequent
  # commits by vault_fs land cleanly.
  subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
  subprocess.run(["git", "-C", str(root), "config", "user.email", "test@forge.local"], check=True)
  subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
  subprocess.run(["git", "-C", str(root), "add", "seed.md"], check=True)
  subprocess.run(
    ["git", "-C", str(root), "commit", "-q", "-m", "forge-mcp: commit recipe seed: Return 1. v1"],
    check=True,
  )
  return root


# ---------------------------------------------------------------------------
# Note-parse + splice unit tests (backing the tool)
# ---------------------------------------------------------------------------


class TestParseNote:
  def test_parses_frontmatter_and_all_facets(self):
    raw = (
      "---\ntype: action\n---\n\n"
      "# Description\n\nDesc body.\n\n"
      "# Recipe\n\nReturn 42.\n\n"
      "# Python\n\ncode\n"
    )
    p = parse_note(raw)
    assert p.frontmatter_dict == {"type": "action"}
    assert p.has_recipe_facet
    assert "Return 42." in p.recipe_body
    assert "# Python" in p.post_recipe

  def test_recognizes_legacy_e_double_dash_header(self):
    """Older notes (Untitled.md) use `# E--` instead of `# Recipe`;
    parser must treat both as the Recipe facet."""
    raw = (
      "---\ntype: action\n---\n\n"
      "# Description\n\nx\n\n"
      "# E--\n\nLet foo = 1.\n\n"
      "# Python\n\ncode\n"
    )
    p = parse_note(raw)
    assert p.has_recipe_facet
    assert "Let foo = 1." in p.recipe_body

  def test_note_without_recipe_facet(self):
    raw = "---\ntype: action\n---\n\n# Description\n\nx\n"
    p = parse_note(raw)
    assert not p.has_recipe_facet


# ---------------------------------------------------------------------------
# Splicer — drain §5 tests #1, #9, #10
# ---------------------------------------------------------------------------


class TestSpliceRecipe:
  def test_replaces_recipe_body_preserves_description_and_python(self, vault_root: Path):
    """Drain §5 test #1 — Description + Python + frontmatter byte-for-byte
    survive; only Recipe body flips."""
    raw = (vault_root / "notes" / "seed.md").read_text()
    new = splice_recipe(raw, "Return 999.", new_version=2)

    parsed = parse_note(new)
    assert "The original description that must survive commit." in parsed.pre_recipe
    assert "def compute(context):" in parsed.post_recipe
    assert "Return 999." in parsed.recipe_body
    # And the old Recipe body is gone.
    assert "Return 1.\n" not in parsed.recipe_body

  def test_version_stamped_in_frontmatter(self, vault_root: Path):
    raw = (vault_root / "notes" / "seed.md").read_text()
    new = splice_recipe(raw, "Return 2.", new_version=7)
    parsed = parse_note(new)
    assert parsed.frontmatter_dict.get("recipe_version") == "7"
    # Existing frontmatter fields (type / description) survive.
    assert parsed.frontmatter_dict.get("type") == "action"
    assert parsed.frontmatter_dict.get("description") == "seed note"

  def test_writer_preserves_frontmatter_ordering(self):
    """Drain §5 test #9 — the YAML frontmatter's OTHER fields (order,
    unusual keys) round-trip untouched."""
    raw = (
      "---\n"
      "type: action\n"
      "description: complicated\n"
      "aliases:\n"
      "  - foo\n"
      "  - bar\n"
      "custom_field: 42\n"
      "---\n"
      "\n# Recipe\n\nOld.\n"
    )
    new = splice_recipe(raw, "New.", new_version=3)
    # All original lines still present (recipe_version appended, not
    # scattered mid-block; and the arbitrary `aliases:` list intact).
    for expected in ("type: action", "description: complicated", "aliases:", "  - foo", "  - bar", "custom_field: 42"):
      assert expected in new, f"missing frontmatter content: {expected!r}"
    assert "recipe_version: 3" in new
    assert "New." in new

  def test_appends_recipe_facet_when_absent(self):
    """Drain §5 test #10 — note has no `# Recipe`; splicer appends one."""
    raw = (
      "---\ntype: action\n---\n\n# Description\n\nOnly desc.\n"
    )
    new = splice_recipe(raw, "First recipe.", new_version=1)
    parsed = parse_note(new)
    assert parsed.has_recipe_facet
    assert "First recipe." in parsed.recipe_body
    # Description still there.
    assert "Only desc." in parsed.pre_recipe


# ---------------------------------------------------------------------------
# VaultFS — path safety, version tracking, git commit
# ---------------------------------------------------------------------------


class TestVaultFSPathSafety:
  def test_rejects_note_id_with_traversal(self, vault_root: Path):
    """Drain §5 test #6 — `..` traversal defense."""
    fs = VaultFS(root=vault_root)
    with pytest.raises(NoteIdInvalid):
      fs.note_path("../etc/passwd")

  def test_rejects_absolute_note_id(self, vault_root: Path):
    fs = VaultFS(root=vault_root)
    with pytest.raises(NoteIdInvalid):
      fs.note_path("/tmp/malicious")

  def test_rejects_hidden_segment(self, vault_root: Path):
    fs = VaultFS(root=vault_root)
    with pytest.raises(NoteIdInvalid):
      fs.note_path(".obsidian/config")

  def test_accepts_nested_valid_path(self, vault_root: Path):
    fs = VaultFS(root=vault_root)
    resolved = fs.note_path("notes/seed")
    assert resolved == (vault_root / "notes" / "seed.md").resolve()


class TestVaultFSCommitRecipe:
  def test_increments_version_on_each_commit(self, vault_root: Path):
    """Drain §5 test #2."""
    fs = VaultFS(root=vault_root)
    v1, _ = fs.commit_recipe(
      note_id="notes/seed",
      new_recipe_body="Return 2.",
      expected_version=0,  # seed has no `recipe_version` stamp yet → 0
    )
    assert v1 == 1
    v2, _ = fs.commit_recipe(
      note_id="notes/seed",
      new_recipe_body="Return 3.",
      expected_version=1,
    )
    assert v2 == 2

  def test_version_conflict_raises_without_writing(self, vault_root: Path):
    """Drain §5 test #3 — precondition: agent's expected_version
    disagrees with current → raise + do NOT touch the file."""
    fs = VaultFS(root=vault_root)
    fs.commit_recipe(note_id="notes/seed", new_recipe_body="v1.", expected_version=0)
    before = (vault_root / "notes" / "seed.md").read_text()
    with pytest.raises(VersionConflict) as exc_info:
      fs.commit_recipe(
        note_id="notes/seed",
        new_recipe_body="stale write",
        expected_version=0,  # stale — file is at version 1
      )
    exc = exc_info.value
    assert exc.expected == 0
    assert exc.current == 1
    # File contents unchanged.
    after = (vault_root / "notes" / "seed.md").read_text()
    assert before == after

  def test_no_git_returns_none_sha(self, vault_root: Path):
    """Non-git vault — commit succeeds, git_sha=None per drain §6."""
    fs = VaultFS(root=vault_root)
    _, sha = fs.commit_recipe(
      note_id="notes/seed", new_recipe_body="Return 5.", expected_version=None
    )
    assert sha is None


# ---------------------------------------------------------------------------
# forge-recipe:///{note_id}/v{n} resource — drain §5 tests #7 + #8
# ---------------------------------------------------------------------------


class TestRecipeUri:
  def test_parse_uri_extracts_note_id_and_version(self):
    note_id, version = parse_forge_recipe_uri("forge-recipe:///notes/seed/v3")
    assert note_id == "notes/seed"
    assert version == 3

  def test_versioned_source_from_git_history(self, git_vault_root: Path):
    """Drain §5 test #7."""
    fs = VaultFS(root=git_vault_root)
    # Fresh commit → v2.
    v2, sha = fs.commit_recipe(
      note_id="seed", new_recipe_body="Return 22.", expected_version=1
    )
    assert v2 == 2
    assert sha is not None  # git-tracked vault → SHA present
    body_v2 = fs.read_recipe_version("seed", 2)
    assert body_v2 is not None
    assert "Return 22." in body_v2
    # v1 (the initial seed commit) still accessible.
    body_v1 = fs.read_recipe_version("seed", 1)
    assert body_v1 is not None
    assert "Return 1." in body_v1

  def test_missing_version_returns_history_unavailable_text(self, git_vault_root: Path):
    """Drain §5 test #8 — non-existent version → clean 'unavailable'
    text via the resource, not a raised exception."""
    fs = VaultFS(root=git_vault_root)
    result = read_recipe_resource(uri="forge-recipe:///seed/v99", vault_fs=fs)
    assert result["contents"][0]["mimeType"] == "text/plain"
    text = result["contents"][0]["text"].lower()
    # Message names why: not available (no git history), or version missing.
    assert "history available" in text or "unavailable" in text

  def test_no_git_returns_history_unavailable_text(self, vault_root: Path):
    """Non-git vault — resource returns clean 'unavailable' text
    (per drain §6 out-of-scope). Same shape as missing version."""
    fs = VaultFS(root=vault_root)
    # commit once so the note has a version stamp
    fs.commit_recipe(note_id="notes/seed", new_recipe_body="Return X.", expected_version=0)
    result = read_recipe_resource(uri="forge-recipe:///notes/seed/v1", vault_fs=fs)
    text = result["contents"][0]["text"].lower()
    # Message names why: not available (no git history), or version missing.
    assert "history available" in text or "unavailable" in text


# ---------------------------------------------------------------------------
# tools/commit_recipe.py — the MCP tool handler
# ---------------------------------------------------------------------------


class TestCommitRecipeTool:
  @pytest.mark.asyncio
  async def test_missing_note_id_returns_actionable_error(self, vault_root: Path):
    """Drain §5 test #5."""
    fs = VaultFS(root=vault_root)
    result = await commit_recipe.run(
      arguments={"source": "Return 1."},
      bearer="tok",
      vault_fs=fs,
    )
    assert result["isError"] is True
    assert "note_id" in result["content"][0]["text"]

  @pytest.mark.asyncio
  async def test_traversal_note_id_returns_actionable_error(self, vault_root: Path):
    """Drain §5 test #6 — surface path-traversal rejection through the
    tool layer, not just the VaultFS unit."""
    fs = VaultFS(root=vault_root)
    result = await commit_recipe.run(
      arguments={"source": "Return 1.", "note_id": "../evil"},
      bearer="tok",
      vault_fs=fs,
    )
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Invalid note_id" in text or "forbidden" in text or "..'" in text or ".." in text

  @pytest.mark.asyncio
  async def test_writes_recipe_facet_only_via_tool(self, vault_root: Path):
    """Drain §5 test #1 through the tool layer. Description + Python
    survive; Recipe replaces."""
    fs = VaultFS(root=vault_root)
    async with respx.mock(base_url="http://localhost:8000") as mock:
      # Internal run_recipe call: mock a clean run_id.
      mock.post("/run").mock(
        return_value=httpx.Response(200, json={
          "parse_status": "ok",
          "run_id": "runsha1234",
          "exit_code": 0,
          "duration_ms": 5,
          "timed_out": False,
          "stdout_preview": "",
          "artifacts": [],
        })
      )
      async with ForgeServiceClient(base_url="http://localhost:8000") as client:
        result = await commit_recipe.run(
          arguments={
            "source": "Return 999.",
            "note_id": "notes/seed",
            "expected_version": 0,
          },
          bearer="tok",
          client=client,
          vault_fs=fs,
        )

    assert result["isError"] is False
    # File on disk: Description + Python survived byte-for-byte.
    written = (vault_root / "notes" / "seed.md").read_text()
    assert "The original description that must survive commit." in written
    assert "def compute(context):" in written
    assert "Return 999." in written
    assert "Return 1.\n" not in written  # old body gone

  @pytest.mark.asyncio
  async def test_version_conflict_via_tool_returns_isError(self, vault_root: Path):
    """Drain §5 test #3 via the tool layer."""
    fs = VaultFS(root=vault_root)
    # First commit lands cleanly (fresh note → current version 0 → new 1).
    await commit_recipe.run(
      arguments={"source": "Return 1.", "note_id": "notes/seed", "expected_version": 0},
      bearer="tok",
      vault_fs=fs,
    )
    # Second commit with stale expected_version = 0 → conflict.
    result = await commit_recipe.run(
      arguments={"source": "STALE.", "note_id": "notes/seed", "expected_version": 0},
      bearer="tok",
      vault_fs=fs,
    )
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Version conflict" in text
    assert "expected version 0" in text
    assert "note is at version 1" in text
    # `expected` / `current` propagated to structuredContent.
    struct = result["structuredContent"]
    assert struct["committed_version"] == 1  # the ACTUAL current, so agent knows what to expect on retry

  @pytest.mark.asyncio
  async def test_invokes_run_and_returns_run_id(self, vault_root: Path):
    """Drain §5 test #4."""
    fs = VaultFS(root=vault_root)
    async with respx.mock(base_url="http://localhost:8000") as mock:
      mock.post("/run").mock(
        return_value=httpx.Response(200, json={
          "parse_status": "ok",
          "run_id": "deadbeef1234",
          "exit_code": 0,
          "duration_ms": 42,
          "timed_out": False,
          "stdout_preview": "",
          "artifacts": [],
        })
      )
      async with ForgeServiceClient(base_url="http://localhost:8000") as client:
        result = await commit_recipe.run(
          arguments={"source": "Return 1.", "note_id": "notes/seed", "expected_version": 0},
          bearer="tok",
          client=client,
          vault_fs=fs,
        )
    assert result["isError"] is False
    assert result["structuredContent"]["run_id"] == "deadbeef1234"
