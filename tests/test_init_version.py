"""CW-forge-mcp-sync-init-version-to-pyproject (drain 2026-07-28-1500).

`forge_mcp.__version__` is driven by importlib.metadata reading the
installed distribution's Version field, which is populated from
pyproject.toml at install time. This test guards against reintroducing
a hardcoded literal that drifts from pyproject.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _pyproject_version() -> str:
  root = Path(__file__).resolve().parents[1]
  data = tomllib.loads((root / "pyproject.toml").read_text())
  return data["project"]["version"]


def test_version_available():
  """The public `__version__` attribute exists and is a semver-ish string."""
  import forge_mcp

  assert isinstance(forge_mcp.__version__, str)
  # semver-ish or dev-fallback — either shape is allowed by the module.
  assert re.match(r"^\d+\.\d+\.\d+", forge_mcp.__version__), forge_mcp.__version__


def test_version_matches_pyproject_when_installed():
  """When forge-recipe-mcp is installed (pip -e or wheel), `__version__`
  matches pyproject.toml's version. Skipped only if the dev-fallback
  fires because no distribution metadata is on the path.
  """
  from importlib.metadata import PackageNotFoundError, version

  import forge_mcp

  try:
    installed = version("forge-recipe-mcp")
  except PackageNotFoundError:
    # Dev checkout with no metadata — `__version__` is the fallback.
    assert forge_mcp.__version__ == "0.0.0+dev"
    return

  assert forge_mcp.__version__ == installed
  # And installed metadata must agree with pyproject — this is the whole
  # point of the drain: no drift between the two.
  assert installed == _pyproject_version(), (
    f"installed metadata ({installed}) drifts from pyproject "
    f"({_pyproject_version()}). Reinstall with `pip install -e .` "
    "to resync."
  )


def test_no_hardcoded_version_literal_in_init():
  """Guard: `__init__.py` must not carry a hardcoded `__version__ = "..."`
  literal that would silently drift from pyproject.toml.

  This is the regression protection for drain 2026-07-28-1500.
  """
  init = Path(__file__).resolve().parents[1] / "src" / "forge_mcp" / "__init__.py"
  src = init.read_text()
  # Any assignment of the form `__version__ = "x.y.z"` (bare string
  # literal, no importlib call) reintroduces the drift.
  assert not re.search(r'^__version__\s*=\s*"[^"]+"\s*$', src, re.MULTILINE), (
    "forge_mcp/__init__.py has a hardcoded __version__ literal; it must "
    "come from importlib.metadata to avoid drifting from pyproject.toml."
  )
