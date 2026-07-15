"""Drain CW-MCP-cc-integration — `main()` transport picker.

`forge_mcp.server.main()` now reads `FORGE_MCP_TRANSPORT` (default
`streamable-http` for back-compat). Invalid values SystemExit with a
message naming the valid options so the driver can fix the shell
env without poking source.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from forge_mcp import server as server_mod


def test_invalid_transport_env_raises_systemexit(monkeypatch):
  monkeypatch.setenv("FORGE_MCP_TRANSPORT", "carrier-pigeon")
  with pytest.raises(SystemExit) as exc_info:
    server_mod.main()
  # Message names both the offending value AND the valid set so the
  # driver knows what to type instead.
  assert "carrier-pigeon" in str(exc_info.value)
  assert "stdio" in str(exc_info.value)
  assert "streamable-http" in str(exc_info.value)


def test_stdio_transport_forwards_to_server_run(monkeypatch):
  """`FORGE_MCP_TRANSPORT=stdio` calls FastMCP.run with transport='stdio'."""
  monkeypatch.setenv("FORGE_MCP_TRANSPORT", "stdio")
  with patch.object(server_mod, "_make_server") as mk:
    mk.return_value.run = lambda transport: setattr(mk.return_value, "_seen", transport)
    server_mod.main()
  assert mk.return_value._seen == "stdio"


def test_default_transport_is_streamable_http(monkeypatch):
  """No `FORGE_MCP_TRANSPORT` in env → streamable-http (unchanged from
  the pre-drain default; no regression for hosted deployments)."""
  monkeypatch.delenv("FORGE_MCP_TRANSPORT", raising=False)
  with patch.object(server_mod, "_make_server") as mk:
    mk.return_value.run = lambda transport: setattr(mk.return_value, "_seen", transport)
    server_mod.main()
  assert mk.return_value._seen == "streamable-http"
