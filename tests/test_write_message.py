"""CW-forge-mcp-write-message-tool (drain 2026-07-29-0900)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from forge_mcp.tools import write_message


@pytest.fixture(autouse=True)
def _messages_root(tmp_path: Path, monkeypatch):
  """Route every test's writes to an isolated tmp directory."""
  root = tmp_path / "messages"
  root.mkdir()
  monkeypatch.setenv("FORGE_MCP_MESSAGES_ROOT", str(root))
  return root


@pytest.mark.asyncio
async def test_writes_message_from_wizard_to_forge_core(_messages_root: Path):
  """Acceptance #2: happy path — creates file at expected path with correct metadata."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": "test message"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  structured = result["structuredContent"]
  path = Path(structured["path"])
  assert path.is_file()
  assert path.parent == _messages_root / "to-forge-core" / "from-wizard"
  assert path.read_text() == "test message"
  assert structured["to"] == "forge-core"
  assert structured["from"] == "wizard"
  assert structured["size_bytes"] == len("test message".encode("utf-8"))
  assert structured["sha256"] == hashlib.sha256(b"test message").hexdigest()
  # ISO timestamp shape (YYYY-MM-DDTHH:MM:SSZ).
  assert len(structured["timestamp_utc"]) == len("2026-01-01T00:00:00Z")
  assert structured["timestamp_utc"].endswith("Z")
  # Filename: YYYY-MM-DD-HHMM-<slug>.md.
  assert path.name.endswith(".md")


@pytest.mark.asyncio
async def test_refuses_non_whitelisted_target(_messages_root: Path):
  """Acceptance #3: unknown target rejected."""
  result = await write_message.run(
    arguments={"to": "anywhere", "body": "x"},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "not in the allowed target set" in result["content"][0]["text"]
  # Nothing written.
  assert list(_messages_root.rglob("*.md")) == []


@pytest.mark.asyncio
async def test_refuses_non_whitelisted_source(_messages_root: Path):
  """Acceptance #4: unknown source rejected."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": "x", "from": "fake-cowork"},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "not in the allowed source set" in result["content"][0]["text"]
  assert list(_messages_root.rglob("*.md")) == []


@pytest.mark.asyncio
async def test_refuses_slug_with_slash(_messages_root: Path):
  """Acceptance #5a: slug with path separator rejected."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": "x", "slug": "a/b"},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "path separator" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_refuses_slug_with_double_dot(_messages_root: Path):
  """Acceptance #5b: slug with '..' rejected."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": "x", "slug": "a..b"},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "'..'" in result["content"][0]["text"] or "traversal" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_auto_slug_from_body(_messages_root: Path):
  """Acceptance #6: auto-slug takes first 5-8 words, kebab-case, ≤60 chars."""
  body = "# Header line\n\nThis is a much longer body with many many many many many many words that should be truncated"
  result = await write_message.run(
    arguments={"to": "forge-core", "body": body},
    bearer="tok",
  )
  assert result["isError"] is False, result
  path = Path(result["structuredContent"]["path"])
  # Filename: YYYY-MM-DD-HHMM-<slug>.md; slug is the part after HHMM- and before .md.
  # Header token stripped by markdown-cleanup regex.
  # First 8 words of the cleaned body: "Header line This is a much longer body".
  slug = path.stem.split("-", 4)[-1]  # after date-HHMM
  assert "-" in slug
  # Slug should be kebab-cased, all lowercase, no whitespace.
  assert slug == slug.lower()
  assert " " not in slug
  # Cap at 60 chars.
  assert len(slug) <= 60
  # Contains meaningful early words.
  assert "header" in slug or "line" in slug or "this" in slug


@pytest.mark.asyncio
async def test_auto_slug_from_empty_body(_messages_root: Path):
  """Auto-slug falls back to 'message' on empty body."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": ""},
    bearer="tok",
  )
  assert result["isError"] is False, result
  path = Path(result["structuredContent"]["path"])
  assert path.stem.endswith("-message")


@pytest.mark.asyncio
async def test_refuses_body_over_max_size(_messages_root: Path):
  """Acceptance #7: >max_size_kb rejected."""
  huge = "x" * (200 * 1024)  # 200 KB
  result = await write_message.run(
    arguments={"to": "forge-core", "body": huge},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "max_size_kb" in result["content"][0]["text"]
  assert list(_messages_root.rglob("*.md")) == []


@pytest.mark.asyncio
async def test_max_size_kb_override_accepts_larger(_messages_root: Path):
  """max_size_kb override lifts the default 100 KB cap."""
  huge = "x" * (200 * 1024)  # 200 KB
  result = await write_message.run(
    arguments={"to": "forge-core", "body": huge, "max_size_kb": 500},
    bearer="tok",
  )
  assert result["isError"] is False, result
  path = Path(result["structuredContent"]["path"])
  assert path.stat().st_size == 200 * 1024


@pytest.mark.asyncio
async def test_collision_suffix(_messages_root: Path):
  """Acceptance #8: filename collision handled via -2, -3, ... suffix."""
  # Use explicit slug so we can predict collision (auto-slug varies by body).
  args = {"to": "forge-core", "body": "content one", "slug": "same-slug"}
  first = await write_message.run(arguments=args, bearer="tok")
  assert first["isError"] is False, first
  first_path = Path(first["structuredContent"]["path"])
  assert first_path.stem.endswith("-same-slug")

  # Second write within the same minute → -2 suffix.
  second = await write_message.run(
    arguments={"to": "forge-core", "body": "content two", "slug": "same-slug"},
    bearer="tok",
  )
  assert second["isError"] is False, second
  second_path = Path(second["structuredContent"]["path"])
  assert second_path != first_path
  assert second_path.stem.endswith("-same-slug-2")

  # Third → -3.
  third = await write_message.run(
    arguments={"to": "forge-core", "body": "content three", "slug": "same-slug"},
    bearer="tok",
  )
  assert third["isError"] is False, third
  third_path = Path(third["structuredContent"]["path"])
  assert third_path.stem.endswith("-same-slug-3")

  # Both files still exist with distinct content.
  assert first_path.read_text() == "content one"
  assert second_path.read_text() == "content two"
  assert third_path.read_text() == "content three"


# ---------------------------------------------------------------------------
# Extra safety tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_insensitive_target_normalization(_messages_root: Path):
  """`to='Forge-Core'` normalizes to `forge-core` (dir name lowercase)."""
  result = await write_message.run(
    arguments={"to": "Forge-Core", "body": "x"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  path = Path(result["structuredContent"]["path"])
  assert path.parent.name == "from-wizard"
  assert path.parent.parent.name == "to-forge-core"


@pytest.mark.asyncio
async def test_refuses_slug_with_hidden_prefix(_messages_root: Path):
  """Slug starting with '.' rejected (hidden segment guard)."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": "x", "slug": ".hidden"},
    bearer="tok",
  )
  assert result["isError"] is True


@pytest.mark.asyncio
async def test_creates_parent_directories(_messages_root: Path):
  """`from=` for a new source (never messaged before) auto-creates dirs."""
  # Precondition: dir doesn't exist.
  assert not (_messages_root / "to-forge-reviewer" / "from-wizard").exists()
  result = await write_message.run(
    arguments={"to": "forge-reviewer", "body": "hi"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  assert (_messages_root / "to-forge-reviewer" / "from-wizard").is_dir()


@pytest.mark.asyncio
async def test_custom_source_normalizes(_messages_root: Path):
  """Non-wizard sources routed to `from-<source>/` correctly."""
  result = await write_message.run(
    arguments={"to": "forge-core", "body": "x", "from": "ccqa"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  path = Path(result["structuredContent"]["path"])
  assert path.parent.name == "from-ccqa"
