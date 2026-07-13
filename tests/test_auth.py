"""CW-MCP-1-B — auth extraction + forwarding tests.

Covers the 5 cases the drain requires:
  (a) missing Authorization → tool result surfaces AUTH_MISSING
  (b) malformed Authorization → surfaces AUTH_MALFORMED
  (c) valid Bearer forwards to forge_service_client verbatim
  (d) forge_service returning 401 → MCP isError with invalid-token wording
  (e) forge_service returning 200 → passthrough with note data intact

The pure `extract_bearer_from_header` cases live at the top of the file;
the integration cases exercise the full tool run() through respx-mocked
httpx.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from forge_mcp.auth import (
  BearerMalformedError,
  BearerMissingError,
  extract_bearer_from_header,
  extract_bearer_from_request,
)
from forge_mcp.forge_service_client import ForgeServiceClient
from forge_mcp.tools import read_note_catalog


class _StubHeaders:
  """Minimal Starlette-headers-like stub for the tests."""

  def __init__(self, mapping: dict[str, str]) -> None:
    self._m = {k.lower(): v for k, v in mapping.items()}

  def get(self, name: str, default: str | None = None) -> str | None:
    return self._m.get(name.lower(), default)


class _StubRequest:
  """Minimal Starlette-request-like stub."""

  def __init__(self, headers: dict[str, str]) -> None:
    self.headers = _StubHeaders(headers)


# -----------------------------------------------------------------------------
# Pure header parsing (case a + b)
# -----------------------------------------------------------------------------


def test_extract_bearer_missing_header_raises_missing_error() -> None:
  with pytest.raises(BearerMissingError):
    extract_bearer_from_header(None)


def test_extract_bearer_empty_string_raises_missing_error() -> None:
  with pytest.raises(BearerMissingError):
    extract_bearer_from_header("   ")


def test_extract_bearer_wrong_scheme_raises_malformed() -> None:
  with pytest.raises(BearerMalformedError):
    extract_bearer_from_header("Basic dXNlcjpwYXNz")


def test_extract_bearer_no_space_raises_malformed() -> None:
  with pytest.raises(BearerMalformedError):
    extract_bearer_from_header("BearerNoSpaceHere")


def test_extract_bearer_empty_token_raises_malformed() -> None:
  with pytest.raises(BearerMalformedError):
    extract_bearer_from_header("Bearer ")


def test_extract_bearer_valid_returns_token() -> None:
  ext = extract_bearer_from_header("Bearer secret-token")
  assert ext.token == "secret-token"


def test_extract_bearer_case_insensitive_scheme() -> None:
  # Some clients emit lowercase "bearer" — RFC 6750 says the scheme is
  # case-insensitive, so accept it.
  ext = extract_bearer_from_header("bearer secret-token")
  assert ext.token == "secret-token"


def test_extract_bearer_from_request_missing_headers_attr() -> None:
  class _NoHeaders:
    pass

  with pytest.raises(BearerMissingError):
    extract_bearer_from_request(_NoHeaders())


def test_extract_bearer_from_request_forwards_header_value() -> None:
  req = _StubRequest({"Authorization": "Bearer my-token"})
  ext = extract_bearer_from_request(req)
  assert ext.token == "my-token"


# -----------------------------------------------------------------------------
# Integration: valid Bearer forwarded downstream (case c)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_valid_bearer_forwarded_to_forge_service() -> None:
  route = respx.get("http://localhost:8000/catalog").mock(
    return_value=httpx.Response(
      200,
      json={
        "notes": [
          {
            "name": "voices_canonical",
            "domain": "music",
            "signature": "Call [[voices_canonical]] with kp, chp",
            "short_desc": "compose canonical voice layout",
            "long_desc": "long",
            "uri": "forge-note:///music/voices_canonical",
          }
        ]
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await read_note_catalog.run(
      arguments={},
      bearer="cohort-token",
      client=client,
    )
  assert result["isError"] is False
  # Verify the bearer landed on the actual outbound request verbatim.
  assert route.called
  headers = route.calls.last.request.headers
  assert headers.get("authorization") == "Bearer cohort-token"


# -----------------------------------------------------------------------------
# Integration: upstream 401 → isError with invalid-token wording (case d)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upstream_401_translates_to_isError_invalid_token() -> None:
  respx.get("http://localhost:8000/catalog").mock(
    return_value=httpx.Response(
      401,
      json={"detail": "invalid or missing bearer"},
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await read_note_catalog.run(
      arguments={},
      bearer="stale-token",
      client=client,
    )
  assert result["isError"] is True
  # Message must name the token / auth failure so the agent knows to
  # rotate its credential (not, e.g., retry the request as-is).
  text = result["content"][0]["text"]
  assert "rejected" in text.lower() or "invalid token" in text.lower()
  assert "401" in text
  assert "FORGE_MCP_BEARER" in text  # actionable pointer


@pytest.mark.asyncio
@respx.mock
async def test_upstream_403_also_translates_to_isError_invalid_token() -> None:
  # 403 is treated the same as 401 for token-rejection semantics.
  respx.get("http://localhost:8000/catalog").mock(
    return_value=httpx.Response(
      403,
      json={"detail": "forbidden"},
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await read_note_catalog.run(
      arguments={},
      bearer="wrong-scope-token",
      client=client,
    )
  assert result["isError"] is True
  text = result["content"][0]["text"]
  assert "403" in text
  assert "FORGE_MCP_BEARER" in text


# -----------------------------------------------------------------------------
# Integration: upstream 200 passthrough (case e)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upstream_200_passthrough_returns_notes() -> None:
  respx.get("http://localhost:8000/catalog").mock(
    return_value=httpx.Response(
      200,
      json={
        "notes": [
          {
            "name": "walking_bass_line",
            "domain": "music",
            "signature": "Call [[walking_bass_line]] with harmony",
            "short_desc": "walking upright/electric bass",
            "long_desc": "long",
            "uri": "forge-note:///music/walking_bass_line",
          },
          {
            "name": "form",
            "domain": "music",
            "signature": "Call [[form]]",
            "short_desc": "harmonic form",
            "long_desc": "long",
            "uri": "forge-note:///music/form",
          },
        ]
      },
    )
  )
  async with ForgeServiceClient(base_url="http://localhost:8000") as client:
    result = await read_note_catalog.run(
      arguments={"domain": "music"},
      bearer="valid-token",
      client=client,
    )
  assert result["isError"] is False
  assert len(result["structuredContent"]["notes"]) == 2
  names = {n["name"] for n in result["structuredContent"]["notes"]}
  assert {"walking_bass_line", "form"} == names
