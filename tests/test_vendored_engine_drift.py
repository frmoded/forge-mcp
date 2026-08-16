"""The vendored engine copies must match the engine source byte-for-byte.

Drain 2026-08-17-0100 (sync_state Phase 2). The bundle-subset HARD RULE
says a drift-detection gate ships in the same drain that introduces the
subset — the plugin's engine bundle went 17 releases before its gate
landed, accumulating 5 files of silent drift.

This FAILS rather than skips when the engine repo is absent. A gate that
quietly passes when it cannot run is worse than no gate (I23), and
`check-engine-libs-drift.sh` sets the precedent: "this is a setup error
— the drift check did NOT run. Do not record it as a clean result."
"""
from __future__ import annotations

import os
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[1] / "src" / "forge_mcp" / "_vendor"

# vendored name -> path relative to the engine repo root
VENDORED = {"sync_state.py": "forge/core/sync_state.py"}


def _engine_repo() -> Path:
  env = os.environ.get("ENGINE_REPO")
  if env:
    return Path(env).expanduser().resolve()
  return (Path(__file__).resolve().parents[2] / "forge").resolve()


def test_engine_repo_is_present_so_this_gate_can_actually_run():
  root = _engine_repo()
  assert root.is_dir(), (
    f"engine repo not found at {root}. The drift gate did NOT run — this is "
    f"a setup error, not a clean result. Set ENGINE_REPO=/path/to/forge."
  )


def test_every_vendored_file_matches_its_engine_source():
  root = _engine_repo()
  for name, rel in VENDORED.items():
    source, vendored = root / rel, VENDOR_DIR / name
    assert source.is_file(), f"no engine source at {source}"
    assert vendored.is_file(), f"not vendored: {vendored}"
    assert vendored.read_bytes() == source.read_bytes(), (
      f"DRIFT: {vendored} differs from {source}. Fix the ENGINE source, "
      f"then re-vendor: bash scripts/sync-vendored-engine.sh"
    )


def test_the_vendor_dir_holds_nothing_unmapped():
  """A file nobody mapped is a file nobody checks."""
  present = {
    p.name for p in VENDOR_DIR.glob("*.py") if p.name != "__init__.py"
  }
  assert present == set(VENDORED), (
    f"unmapped vendored files: {present - set(VENDORED)}. Add them to "
    f"VENDORED here and to scripts/sync-vendored-engine.sh, or delete them."
  )


def test_vendored_module_imports_and_exposes_the_contract():
  from forge_mcp._vendor.sync_state import SYNC_STATES, derive_sync_state

  assert set(SYNC_STATES) == {"synced", "stale-recipe", "stale-python", "unknown"}
  assert callable(derive_sync_state)
