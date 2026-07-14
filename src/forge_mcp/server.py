"""forge-mcp server bootstrap.

Wire the two read-only tools and the forge-note resource template onto a
FastMCP instance, then serve via Streamable HTTP.

SDK choice: FastMCP from `mcp.server.fastmcp`. Chosen because the decorator
model gives us clean tool registration without hand-authoring the JSON-RPC
dispatch, and it ships Streamable HTTP transport out of the box
(`run(transport="streamable-http")`).

Env:
  FORGE_MCP_PORT — port to bind. Default 8765.
  FORGE_MCP_HOST — host to bind. Default 0.0.0.0.
  FORGE_MCP_BEARER — DEV FALLBACK ONLY. Used when the incoming Streamable
    HTTP request has no Authorization header (e.g., a locally-run test
    without a full MCP client). CW-MCP-1-B extracts per-request Bearer
    from `Context.request_context.request.headers['authorization']` in
    production; the env var lets the smoke script authenticate against a
    live forge-transpile without wiring a full MCP client.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-untyped]

from . import __version__
from .auth import (
  BearerExtractionError,
  auth_error_to_tool_result,
  extract_bearer_from_request,
)
from .resources.artifact_uri import read_artifact_resource
from .resources.note_uri import parse_forge_note_uri, read_note_resource
from .tools import (
  compile_recipe,
  get_run_result,
  read_note_catalog,
  read_notes_in_vault,
  run_recipe,
)

log = logging.getLogger("forge-mcp")


def _bearer_from_context(ctx: Context) -> str:
  """Extract the per-request Bearer, falling back to FORGE_MCP_BEARER.

  Raises BearerExtractionError when neither the request's Authorization
  header nor the env fallback provide a token. Tool handlers translate
  that exception via `auth_error_to_tool_result` so the agent sees a
  clean `isError: true` with an actionable message.

  Design (CW-MCP-1-B): per-request extraction is the primary path so a
  single forge-mcp instance can serve multiple agents each with their
  own token. The env fallback is dev-only for smoke scripts.
  """
  req_ctx = getattr(ctx, "request_context", None)
  request = getattr(req_ctx, "request", None) if req_ctx else None
  if request is not None:
    try:
      return extract_bearer_from_request(request).token
    except BearerExtractionError:
      # Try the env fallback before propagating the failure — cohort
      # smoke scripts may hit forge-mcp locally without a full MCP
      # client that sets Authorization.
      env_bearer = os.environ.get("FORGE_MCP_BEARER", "").strip()
      if env_bearer:
        return env_bearer
      raise
  # No request context available (shouldn't happen with Streamable HTTP,
  # but be defensive). Fall back to env or raise.
  env_bearer = os.environ.get("FORGE_MCP_BEARER", "").strip()
  if env_bearer:
    return env_bearer
  from .auth import BearerMissingError

  raise BearerMissingError(
    "No Authorization header on the request and FORGE_MCP_BEARER is not "
    "set. Configure your MCP client with 'Bearer <token>' — see "
    "docs/install.md."
  )


def _make_server(
  host: str | None = None,
  port: int | None = None,
) -> FastMCP:
  server: FastMCP = FastMCP(
    name="forge-mcp",
    instructions=(
      "forge-mcp exposes the Forge E-- library note catalog + vault as tools "
      "and library notes as forge-note:///domain/name resources."
    ),
    host=host or os.environ.get("FORGE_MCP_HOST", "0.0.0.0"),
    port=port or int(os.environ.get("FORGE_MCP_PORT", "8765")),
    stateless_http=True,
  )

  # ---------------------------------------------------------------------------
  # Tools
  # ---------------------------------------------------------------------------

  @server.tool(
    name=read_note_catalog.TOOL_NAME,
    description=read_note_catalog.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_read_note_catalog(
    ctx: Context,
    domain: str | None = None,
  ) -> dict[str, Any]:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return auth_error_to_tool_result(exc)
    result = await read_note_catalog.run(
      arguments={"domain": domain} if domain is not None else {},
      bearer=bearer,
    )
    # FastMCP structures the return; downstream MCP clients see both text
    # and structuredContent because of structured_output=True.
    return result

  @server.tool(
    name=compile_recipe.TOOL_NAME,
    description=compile_recipe.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_compile_recipe(
    ctx: Context,
    source: str,
  ) -> dict[str, Any]:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return auth_error_to_tool_result(exc)
    return await compile_recipe.run(
      arguments={"source": source},
      bearer=bearer,
    )

  @server.tool(
    name=run_recipe.TOOL_NAME,
    description=run_recipe.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_run_recipe(
    ctx: Context,
    source: str,
  ) -> dict[str, Any]:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return auth_error_to_tool_result(exc)
    return await run_recipe.run(arguments={"source": source}, bearer=bearer)

  @server.tool(
    name=get_run_result.TOOL_NAME,
    description=get_run_result.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_get_run_result(
    ctx: Context,
    run_id: str,
  ) -> dict[str, Any]:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return auth_error_to_tool_result(exc)
    return await get_run_result.run(arguments={"run_id": run_id}, bearer=bearer)

  @server.tool(
    name=read_notes_in_vault.TOOL_NAME,
    description=read_notes_in_vault.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_read_notes_in_vault(
    ctx: Context,
    filter: str | None = None,
  ) -> dict[str, Any]:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return auth_error_to_tool_result(exc)
    result = await read_notes_in_vault.run(
      arguments={"filter": filter} if filter is not None else {},
      bearer=bearer,
    )
    return result

  # ---------------------------------------------------------------------------
  # Resources — forge-note:///{domain}/{name}
  # ---------------------------------------------------------------------------

  @server.resource(
    "forge-note:///{domain}/{name}",
    name="forge-note",
    description="Library note fetched from the Forge catalog.",
    mime_type="application/vnd.forge.note+json",
  )
  async def _read_forge_note(domain: str, name: str) -> str:
    # FastMCP's resource decorator with a URI template doesn't currently
    # inject a Context param — it treats every param as a path variable.
    # For CW-MCP-1-B we resolve the bearer via the ambient request-scoped
    # context that FastMCP maintains during request handling. This uses
    # `FastMCP.get_context()` inside the running request scope; it raises
    # if called outside a request, which is exactly what we want for
    # bare imports.
    try:
      # Late binding — need the enclosing server instance to reach
      # `get_context()`. Server is captured in the closure via `nonlocal`
      # not being available; use the module-level singleton once we
      # attach it below.
      ctx = server.get_context()
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      # Resource read failures surface via a plain-text `contents` entry
      # so the URI still resolves in the MCP client — the agent sees the
      # rejection text and can rotate its token.
      import json as _json

      return _json.dumps(
        {
          "contents": [
            {
              "uri": f"forge-note:///{domain}/{name}",
              "mimeType": "text/plain",
              "text": str(exc),
            }
          ]
        }
      )
    uri = f"forge-note:///{domain}/{name}"
    # Round-trip through the parser to validate the reconstructed URI.
    parse_forge_note_uri(uri)
    payload = await read_note_resource(uri=uri, bearer=bearer)
    contents = payload.get("contents", [])
    if contents and "text" in contents[0]:
      return contents[0]["text"]
    return "{}"

  # ---------------------------------------------------------------------------
  # Resources — forge-artifact:///{run_id}/{artifact_name}   (CW-MCP-2-B)
  # ---------------------------------------------------------------------------

  @server.resource(
    "forge-artifact:///{run_id}/{artifact_name}",
    name="forge-artifact",
    description=(
      "Binary artifact produced by a forge_run_recipe call. "
      "MusicXML / MIDI / PNG / etc. Text mimes return via `text`; "
      "binaries return base64 via `blob`."
    ),
    # Placeholder — actual mime is per-artifact and set on the
    # returned contents block below.
    mime_type="application/octet-stream",
  )
  async def _read_forge_artifact(run_id: str, artifact_name: str) -> str:
    # Same context escape as _read_forge_note (see CW-MCP-1-B L47 #3).
    try:
      ctx = server.get_context()
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      import json as _json

      return _json.dumps(
        {
          "contents": [
            {
              "uri": f"forge-artifact:///{run_id}/{artifact_name}",
              "mimeType": "text/plain",
              "text": str(exc),
            }
          ]
        }
      )
    uri = f"forge-artifact:///{run_id}/{artifact_name}"
    payload = await read_artifact_resource(uri=uri, bearer=bearer)
    contents = payload.get("contents", [])
    if not contents:
      return "{}"
    # Return the first content block as a JSON string; MCP clients
    # unwrap this into the resource-read response envelope.
    import json as _json

    return _json.dumps(contents[0])

  return server


def main() -> None:
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  )
  log.info("Starting forge-mcp v%s", __version__)
  server = _make_server()
  server.run(transport="streamable-http")


if __name__ == "__main__":
  main()
