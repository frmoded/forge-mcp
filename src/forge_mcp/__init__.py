"""forge-mcp — MCP server exposing the Forge E-- library note catalog + vault."""

from importlib.metadata import PackageNotFoundError, version

try:
  __version__ = version("forge-recipe-mcp")
except PackageNotFoundError:
  # Dev checkout without `pip install -e .` — safe fallback so `import
  # forge_mcp` doesn't crash. Production installs (Docker, systemd, PyPI)
  # always have the metadata available.
  __version__ = "0.0.0+dev"
