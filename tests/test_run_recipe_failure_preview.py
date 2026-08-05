"""The failure preview must carry a usable run_id (drain 2026-08-05-0820).

Wizard hit a failing run, read `Run 97de26b9… — exit=1, 41ms` off the
text, called forge_get_run_result("97de26b9"), and got "not found:
invalid id". Stderr — the one thing they needed — stayed unreachable.

The envelope was never the problem: success and failure return the
identical `structuredContent = result.model_dump()`, full run_id
included. The problem is that `Run 97de26b9… ` READS as "the run id is
97de26b9", and the only thing saying otherwise is a one-character
ellipsis.
"""
from __future__ import annotations

from forge_mcp.tools.run_recipe import _preview_text
from forge_mcp.schemas import RunResult

FULL_ID = "97de26b9c4f14a2d8e6b3f105a7c9e21"


def _result(**over):
  base = dict(
    parse_status="ok", run_id=FULL_ID, duration_ms=41, exit_code=0,
    stdout_preview="", timed_out=False, artifacts=[],
  )
  base.update(over)
  return RunResult(**base)


def test_failed_run_preview_contains_the_full_id():
  text = _preview_text(_result(exit_code=1))
  assert FULL_ID in text, (
    "the full run_id must be readable from the text; the 8-char header "
    "form is for scanning and cannot be pasted back"
  )


def test_failed_run_preview_shows_the_exact_follow_up_call():
  # Actionable beats informative: the caller is mid-failure and wants
  # stderr, not a field name to go look up.
  text = _preview_text(_result(exit_code=1))
  assert f'forge_get_run_result(run_id="{FULL_ID}")' in text


def test_timed_out_run_counts_as_failed():
  text = _preview_text(_result(exit_code=0, timed_out=True))
  assert "This run failed" in text
  assert FULL_ID in text


def test_successful_run_also_offers_the_full_id():
  # Same need, lower urgency — stdout_preview truncates at 1200 chars,
  # so a long successful run still needs the fetch.
  text = _preview_text(_result(exit_code=0))
  assert FULL_ID in text
  assert "This run failed" not in text


def test_short_header_is_still_there_for_scanning():
  text = _preview_text(_result(exit_code=1))
  assert text.startswith(f"Run {FULL_ID[:8]}… — exit=1")


def test_no_run_id_means_no_fetch_instruction():
  # Parse-error envelopes carry run_id="" — there is no run to fetch,
  # and telling someone to call a tool with an empty id is worse than
  # saying nothing.
  text = _preview_text(_result(run_id="", exit_code=1))
  assert "forge_get_run_result" not in text


def test_stdout_preview_survives_the_new_layout():
  text = _preview_text(_result(exit_code=0, stdout_preview="hello world"))
  assert "hello world" in text
