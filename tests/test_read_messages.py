"""CW-forge-mcp-read-message-tool (drain 2026-07-29-1300)."""
from __future__ import annotations

from pathlib import Path

import pytest

from forge_mcp.tools import read_messages


@pytest.fixture
def _messages_root(tmp_path: Path, monkeypatch):
  root = tmp_path / "messages"
  root.mkdir()
  monkeypatch.setenv("FORGE_MCP_MESSAGES_ROOT", str(root))
  return root


def _write_msg(root: Path, to: str, from_: str, filename: str, body: str) -> Path:
  d = root / "pending" / f"to-{to}" / f"from-{from_}"
  d.mkdir(parents=True, exist_ok=True)
  p = d / filename
  p.write_text(body, encoding="utf-8")
  return p


@pytest.mark.asyncio
async def test_reads_three_wizard_messages_with_full_body(_messages_root: Path):
  """Acceptance #2: 3 messages in wizard inbox → all 3 returned with bodies."""
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-0900-critique-alpha.md", "critique body alpha")
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-1000-critique-beta.md", "critique body beta")
  _write_msg(_messages_root, "wizard", "forge-core",
             "2026-07-29-1100-request-gamma.md", "request body gamma")
  result = await read_messages.run_read(
    arguments={"to": "wizard"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  structured = result["structuredContent"]
  assert structured["total_count"] == 3
  assert structured["returned_count"] == 3
  # Newest first.
  bodies = [m["body"] for m in structured["messages"]]
  assert "request body gamma" in bodies[0]
  # Metadata shape sanity.
  first = structured["messages"][0]
  assert first["from"] == "forge-core"
  assert first["slug"] == "request-gamma"
  assert first["timestamp_utc"].endswith("Z")
  assert first["size_bytes"] > 0


@pytest.mark.asyncio
async def test_unread_only_filters_after_mark_read(_messages_root: Path):
  """Acceptance #3: unread_only=True skips messages moved to done/."""
  m1 = _write_msg(_messages_root, "wizard", "forge-reviewer",
                  "2026-07-29-0900-first.md", "first")
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-1000-second.md", "second")
  # Mark m1 read.
  await read_messages.run_mark(arguments={"path": str(m1)}, bearer="tok")
  # unread_only=True (default) → only second.
  result = await read_messages.run_read(
    arguments={"to": "wizard", "unread_only": True},
    bearer="tok",
  )
  assert result["isError"] is False, result
  assert result["structuredContent"]["total_count"] == 1
  assert result["structuredContent"]["messages"][0]["slug"] == "second"
  # unread_only=False → both, m1 now under read/.
  result_all = await read_messages.run_read(
    arguments={"to": "wizard", "unread_only": False},
    bearer="tok",
  )
  assert result_all["structuredContent"]["total_count"] == 2
  moved = next(
    m for m in result_all["structuredContent"]["messages"] if m["slug"] == "first"
  )
  assert "/read/to-wizard/from-forge-reviewer/" in moved["path"]
  # Drain 1010: `from` survives being marked read — it comes from the
  # from-<sender>/ dir, which the read/ mirror preserves. Under the old
  # flat done/ this reported "(read/moved)".
  assert moved["from"] == "forge-reviewer"


@pytest.mark.asyncio
async def test_since_filters_older_messages(_messages_root: Path):
  """Acceptance #4: since=<ts> returns only messages newer than that ts."""
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-28-0900-old.md", "old")
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-1000-new-one.md", "new one")
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-1100-new-two.md", "new two")
  result = await read_messages.run_read(
    arguments={"to": "wizard", "since": "2026-07-29T00:00:00Z"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  structured = result["structuredContent"]
  assert structured["total_count"] == 2
  slugs = [m["slug"] for m in structured["messages"]]
  assert "new-one" in slugs and "new-two" in slugs
  assert "old" not in slugs


@pytest.mark.asyncio
async def test_refuses_non_whitelisted_target(_messages_root: Path):
  """Acceptance #5: unknown target → clear error."""
  result = await read_messages.run_read(
    arguments={"to": "anywhere"},
    bearer="tok",
  )
  assert result["isError"] is True
  assert "allowed target set" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_body_truncation_over_max_kb(_messages_root: Path):
  """Acceptance #6: bodies > max_body_kb truncated with marker."""
  huge = "x" * (600 * 1024)  # 600 KB
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-0900-huge.md", huge)
  result = await read_messages.run_read(
    arguments={"to": "wizard", "max_body_kb": 100},
    bearer="tok",
  )
  assert result["isError"] is False, result
  msg = result["structuredContent"]["messages"][0]
  assert msg["truncated"] is True
  assert "[TRUNCATED" in msg["body"]
  assert msg["size_bytes"] == 600 * 1024


@pytest.mark.asyncio
async def test_mark_read_moves_file_to_done(_messages_root: Path):
  """Acceptance #5: mark_read moves pending/ → read/, PRESERVING the
  to-<X>/from-<Y>/ structure. Idempotent.

  Drain 1010: the old behaviour flattened into `to-<X>/done/`, which
  destroyed sender attribution the moment a message was read. The
  mirror-tree layout keeps from-<sender>/ on both sides, so this
  asserts the full relative path, not just the parent name."""
  p = _write_msg(_messages_root, "wizard", "forge-reviewer",
                 "2026-07-29-0900-test.md", "body")
  assert p.is_file()
  # First call: moves to read/.
  result = await read_messages.run_mark(
    arguments={"path": str(p)}, bearer="tok",
  )
  assert result["isError"] is False, result
  assert result["structuredContent"]["marked_read"] is True
  new_path = Path(result["structuredContent"]["path"])
  assert new_path.relative_to(_messages_root).parts == (
    "read", "to-wizard", "from-forge-reviewer", "2026-07-29-0900-test.md",
  ), f"sender attribution lost: {new_path}"
  assert new_path.is_file()
  assert not p.exists()  # original moved
  # Second call on the new path: idempotent (already in read/).
  result2 = await read_messages.run_mark(
    arguments={"path": str(new_path)}, bearer="tok",
  )
  assert result2["isError"] is False, result2
  assert result2["structuredContent"]["marked_read"] is True


@pytest.mark.asyncio
async def test_mark_read_idempotent_when_source_moved_by_other(_messages_root: Path):
  """Calling mark_read on the old pending/ path after it's been moved
  finds the moved copy in read/ and reports success.

  Drain 1010: the read/ mirror is from-<sender>-scoped, so the moved
  copy keeps its sender — the lookup rebuilds the same to-/from- pair
  under read/ rather than probing a flat done/."""
  p = _write_msg(_messages_root, "wizard", "forge-reviewer",
                 "2026-07-29-0900-test.md", "body")
  # Simulate someone else moving it first.
  done_dir = _messages_root / "read" / "to-wizard" / "from-forge-reviewer"
  done_dir.mkdir(parents=True)
  moved = done_dir / p.name
  p.rename(moved)
  # Now call mark_read on the original path.
  result = await read_messages.run_mark(
    arguments={"path": str(p)}, bearer="tok",
  )
  assert result["isError"] is False, result
  assert result["structuredContent"]["marked_read"] is True


@pytest.mark.asyncio
async def test_mark_read_refuses_path_outside_messages_root(_messages_root: Path, tmp_path: Path):
  """Path outside messages root rejected."""
  outside = tmp_path / "elsewhere.md"
  outside.write_text("elsewhere")
  result = await read_messages.run_mark(
    arguments={"path": str(outside)}, bearer="tok",
  )
  assert result["isError"] is True
  assert "outside messages root" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_limit_caps_returned_count(_messages_root: Path):
  """limit parameter caps returned_count (total_count reflects full match)."""
  for i in range(30):
    _write_msg(_messages_root, "wizard", "forge-reviewer",
               f"2026-07-29-{9 + i // 60:02d}{i % 60:02d}-msg-{i}.md", f"body {i}")
  result = await read_messages.run_read(
    arguments={"to": "wizard", "limit": 5},
    bearer="tok",
  )
  assert result["isError"] is False, result
  structured = result["structuredContent"]
  assert structured["total_count"] == 30
  assert structured["returned_count"] == 5


@pytest.mark.asyncio
async def test_empty_inbox_returns_zero(_messages_root: Path):
  """No inbox dir → empty result, not an error."""
  result = await read_messages.run_read(
    arguments={"to": "wizard"},
    bearer="tok",
  )
  assert result["isError"] is False, result
  assert result["structuredContent"]["total_count"] == 0
  assert result["structuredContent"]["returned_count"] == 0
  assert result["structuredContent"]["messages"] == []


# ---------------------------------------------------------------
# drain 2026-07-31-1110 — from-legacy/ displays as "(unknown)".

@pytest.mark.asyncio
async def test_drain_1110_from_legacy_reports_unknown(_messages_root: Path):
  """`legacy` is the migration's directory convention; the semantic
  truth is the sender was lost. Report the truth, not the plumbing."""
  d = _messages_root / "read" / "to-wizard" / "from-legacy"
  d.mkdir(parents=True)
  (d / "2026-07-01-0900-old.md").write_text("body", encoding="utf-8")
  result = await read_messages.run_read(
    arguments={"to": "wizard", "unread_only": False}, bearer="tok",
  )
  assert result["isError"] is False, result
  msgs = result["structuredContent"]["messages"]
  assert len(msgs) == 1, msgs
  assert msgs[0]["from"] == "(unknown)", msgs[0]
  assert "legacy" not in msgs[0]["from"]


@pytest.mark.asyncio
async def test_drain_1110_real_senders_still_report_their_name(_messages_root: Path):
  """The legacy special-case must not swallow genuine attribution."""
  _write_msg(_messages_root, "wizard", "forge-reviewer",
             "2026-07-29-0900-real.md", "body")
  result = await read_messages.run_read(
    arguments={"to": "wizard"}, bearer="tok",
  )
  msgs = result["structuredContent"]["messages"]
  assert msgs[0]["from"] == "forge-reviewer", msgs[0]
