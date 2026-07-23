"""HTTP client that talks to forge-transpile for the catalog + vault reads.

Both endpoints are currently missing on forge-transpile — the drain spec
(CW-MCP-1-A §3, and my parent's investigation note) explicitly says to build
the client to call `/catalog` and `/vault/notes` and surface the gap when
they 404. See FEEDBACK §L47.

Environment:
  FORGE_TRANSPILE_URL — base URL. Default: http://localhost:8000.
"""
from __future__ import annotations

import os

import httpx

from .schemas import CompileResult, GetRunResult, NoteEntry, RunResult

_DEFAULT_BASE_URL = "http://localhost:8000"


def _base_url() -> str:
  return os.environ.get("FORGE_TRANSPILE_URL", _DEFAULT_BASE_URL).rstrip("/")


class ForgeServiceError(Exception):
  """Base class for forge-service client errors."""


class ForgeServiceEndpointMissing(ForgeServiceError):
  """Raised when a required forge-transpile endpoint returns 404.

  Not a bug in the caller — a signal that forge-transpile hasn't shipped
  the endpoint yet. The MCP tools translate this to a business-level
  isError=true response so the agent gets an actionable message.
  """

  def __init__(self, endpoint: str, base_url: str) -> None:
    self.endpoint = endpoint
    self.base_url = base_url
    super().__init__(
      f"forge-transpile at {base_url} does not expose {endpoint}. "
      f"See CW-MCP-1-A FEEDBACK §L47 — endpoint has not been implemented yet."
    )


class ForgeServiceHTTPError(ForgeServiceError):
  """Raised when forge-transpile returns a non-2xx that isn't 404."""

  def __init__(self, status_code: int, url: str, body: str) -> None:
    self.status_code = status_code
    self.url = url
    self.body = body
    super().__init__(
      f"forge-transpile returned HTTP {status_code} for {url}: {body[:200]}"
    )


class ForgeServiceClient:
  """Async client wrapping the forge-transpile REST surface we care about."""

  def __init__(
    self,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
  ) -> None:
    self._base_url = (base_url or _base_url()).rstrip("/")
    self._client = client
    self._timeout = timeout
    self._owns_client = client is None

  async def __aenter__(self) -> ForgeServiceClient:
    if self._client is None:
      self._client = httpx.AsyncClient(timeout=self._timeout)
    return self

  async def __aexit__(self, exc_type, exc, tb) -> None:
    if self._owns_client and self._client is not None:
      await self._client.aclose()
      self._client = None

  async def _client_or_ephemeral(self) -> httpx.AsyncClient:
    if self._client is not None:
      return self._client
    # Ephemeral single-request client. Callers who care about pooling
    # should use `async with ForgeServiceClient(...) as c:`.
    return httpx.AsyncClient(timeout=self._timeout)

  @staticmethod
  def _headers(bearer: str) -> dict[str, str]:
    return {
      "Authorization": f"Bearer {bearer}",
      "Accept": "application/json",
    }

  # ---------------------------------------------------------------------------
  # /catalog
  # ---------------------------------------------------------------------------

  async def get_catalog(
    self, domain: str | None, bearer: str
  ) -> list[NoteEntry]:
    """Fetch the library note catalog from forge-transpile.

    # TODO(CW-MCP-1-A follow-up): forge-transpile does not yet expose
    # /catalog; introspection lives in
    # forge-transpile/engine_chip_introspector.py::introspect_engine_chips
    # but isn't wired up as an HTTP endpoint. See FEEDBACK §L47.
    """
    url = f"{self._base_url}/catalog"
    params: dict[str, str] = {}
    if domain is not None:
      params["domain"] = domain

    client = await self._client_or_ephemeral()
    try:
      resp = await client.get(url, params=params, headers=self._headers(bearer))
    finally:
      if self._client is None:
        await client.aclose()

    if resp.status_code == 404:
      raise ForgeServiceEndpointMissing("/catalog", self._base_url)
    if resp.status_code >= 400:
      raise ForgeServiceHTTPError(resp.status_code, url, resp.text)

    # Drain 2670 — forge-transpile's /catalog currently returns a bare
    # JSON array (`response_model=list[NoteEntry]` per drain 1330's
    # main.py). CW-MCP-1-A originally assumed a wrapped `{"notes": [...]}`
    # shape and silently dropped the payload when the wire didn't
    # match, surfacing as "No notes found" isError: true. Accept EITHER
    # shape so a future migration to a wrapped envelope also works
    # without another client change.
    payload = resp.json()
    if isinstance(payload, list):
      raw_notes = payload
    elif isinstance(payload, dict):
      raw_notes = payload.get("notes", [])
    else:
      raw_notes = []
    return [NoteEntry.model_validate(n) for n in raw_notes]

  # /vault/notes was RETIRED in CW-MCP-2-E — forge-transpile never
  # implemented the endpoint, and forge-mcp's `forge_read_notes_in_vault`
  # tool now reads locally via VaultFS (same architecture as
  # forge_commit_recipe in CW-MCP-2-C). If you're looking for that
  # code path, see `~/projects/forge-mcp/src/forge_mcp/vault_fs.py::list_notes`.

  # ---------------------------------------------------------------------------
  # /compile — CW-MCP-2-A
  # ---------------------------------------------------------------------------

  async def compile_recipe(
    self, source: str, bearer: str
  ) -> CompileResult:
    """Deterministically transpile an E-- Recipe to Python.

    forge-transpile's /compile returns HTTP 200 for BOTH success and
    parse errors — the payload's `parse_status` field discriminates.
    Only auth (401/403) and malformed body (422) raise here.
    """
    url = f"{self._base_url}/compile"
    client = await self._client_or_ephemeral()
    try:
      resp = await client.post(
        url,
        json={"source": source},
        headers={**self._headers(bearer), "Content-Type": "application/json"},
      )
    finally:
      if self._client is None:
        await client.aclose()

    if resp.status_code == 404:
      raise ForgeServiceEndpointMissing("/compile", self._base_url)
    if resp.status_code >= 400:
      raise ForgeServiceHTTPError(resp.status_code, url, resp.text)

    return CompileResult.model_validate(resp.json())

  # ---------------------------------------------------------------------------
  # /run + /run/{id}[/artifact/{name}] — CW-MCP-2-B
  # ---------------------------------------------------------------------------

  async def run_recipe(
    self, source: str, bearer: str, domains: list[str] | None = None,
    resolve_slot: dict[str, str] | None = None,
  ) -> RunResult:
    """Compile + execute an E-- Recipe in forge-transpile's sandbox.

    HTTP 200 for BOTH parse errors AND execution failures — the
    `parse_status` field discriminates. Only auth (401/403) and
    malformed bodies (422) raise here.

    `domains` (drain 2900) selects which library-note callables get
    bound into the sandbox namespace. Defaults to `["music"]`; the
    forge-transpile endpoint applies the same default when the field
    is omitted from the wire payload, but we send it explicitly here
    so the outbound request body always names the domain.

    `resolve_slot` (drain 2026-07-21-1405) is an optional map of
    slot-id -> resolved Python snippet, spliced into each `{{ }}`
    slot before sandbox execution. Included in the wire body ONLY
    when non-None + non-empty; back-compat callers that omit it
    keep sending exactly `{source, domains}`.
    """
    url = f"{self._base_url}/run"
    body: dict = {"source": source, "domains": domains or ["music"]}
    if resolve_slot:
      body["resolve_slot"] = resolve_slot
    client = await self._client_or_ephemeral()
    try:
      resp = await client.post(
        url,
        json=body,
        headers={**self._headers(bearer), "Content-Type": "application/json"},
        timeout=60.0,  # longer than the sandbox's own timeout ceiling
      )
    finally:
      if self._client is None:
        await client.aclose()

    if resp.status_code == 404:
      raise ForgeServiceEndpointMissing("/run", self._base_url)
    if resp.status_code >= 400:
      raise ForgeServiceHTTPError(resp.status_code, url, resp.text)
    return RunResult.model_validate(resp.json())

  async def get_run_result(self, run_id: str, bearer: str) -> GetRunResult:
    """Fetch a previously-executed run's full content by run_id.

    404 → ForgeServiceHTTPError (agent-side maps to isError "run not
    found or expired"). Same code covers wrong-user AND wrong-id AND
    expired-run, per the endpoint's isolation-preserving design.
    """
    url = f"{self._base_url}/run/{run_id}"
    client = await self._client_or_ephemeral()
    try:
      resp = await client.get(url, headers=self._headers(bearer))
    finally:
      if self._client is None:
        await client.aclose()

    if resp.status_code >= 400:
      raise ForgeServiceHTTPError(resp.status_code, url, resp.text)
    return GetRunResult.model_validate(resp.json())

  async def fetch_artifact(
    self, run_id: str, artifact_name: str, bearer: str
  ) -> tuple[bytes, str]:
    """Fetch an artifact's binary body + mime type.

    Returns `(body_bytes, mime_type)`. 404 raises ForgeServiceHTTPError
    per the same isolation rationale as `get_run_result`.
    """
    url = f"{self._base_url}/run/{run_id}/artifact/{artifact_name}"
    client = await self._client_or_ephemeral()
    try:
      resp = await client.get(url, headers=self._headers(bearer))
    finally:
      if self._client is None:
        await client.aclose()

    if resp.status_code >= 400:
      raise ForgeServiceHTTPError(resp.status_code, url, resp.text)
    mime = resp.headers.get("content-type", "application/octet-stream")
    return resp.content, mime
