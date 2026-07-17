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
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-untyped]
from mcp.types import CallToolResult, TextContent

from . import __version__
from .auth import (
  BearerExtractionError,
  auth_error_to_tool_result,
  extract_bearer_from_request,
)
from .resources.artifact_uri import read_artifact_resource
from .resources.note_uri import parse_forge_note_uri, read_note_resource
from .resources.recipe_uri import read_recipe_resource
from .tools import (
  commit_recipe,
  compile_recipe,
  create_directory,
  create_note,
  get_run_result,
  list_vaults,
  read_note_catalog,
  read_notes_in_vault,
  run_recipe,
)
from .vault_fs import VaultFS, VaultFSError
from .vault_registry import VaultRegistry, VaultRegistryError

log = logging.getLogger("forge-mcp")


def _to_call_tool_result(payload: dict[str, Any]) -> CallToolResult:
  """Convert an internal `{content, structuredContent, isError}` dict
  (the shape every tool's `run()` returns) into a `CallToolResult`
  object.

  Drain 2026-07-14-1225 — pre-drain, tool handlers returned this dict
  directly to FastMCP, which then treated the WHOLE dict as the
  structured payload and wrapped it again → clients saw
  `structuredContent.structuredContent.<field>` nesting. FastMCP's
  `FuncMetadata.convert_result` (mcp/server/fastmcp/utilities/
  func_metadata.py L114) passes `CallToolResult` instances through
  UNCHANGED, so returning one from the wrapper collapses the double-
  wrap.

  Text content items in `payload["content"]` follow the
  `{type: "text", text: str}` shape — convert each to `TextContent`.
  """
  raw_content = payload.get("content", []) or []
  content_blocks: list[Any] = []
  for item in raw_content:
    if isinstance(item, dict) and item.get("type") == "text":
      content_blocks.append(TextContent(type="text", text=item.get("text", "")))
    else:
      # Defensive — pre-drain shape only ever produced text items;
      # future non-text (image / resource) items would fall through
      # untouched, but at present we don't emit them.
      content_blocks.append(item)
  return CallToolResult(
    content=content_blocks,
    structuredContent=payload.get("structuredContent"),
    isError=bool(payload.get("isError", False)),
  )


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
  vault_registry: VaultRegistry | None = None,
) -> FastMCP:
  # CW-MCP-multi-vault-create-dir: construct the registry once at
  # startup, thread through to every vault-touching tool. If the caller
  # (tests) injects one, use that; otherwise parse from env.
  if vault_registry is None:
    try:
      vault_registry = VaultRegistry.from_env()
    except VaultRegistryError as exc:
      # Fatal — server can't serve vault tools without a usable registry.
      log.error("Vault registry unavailable: %s", exc)
      raise
  # Local capture so nested handlers keep type-narrow reference.
  registry: VaultRegistry = vault_registry
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

  # Drain 2026-07-14-1225 — every tool wrapper returns a
  # `CallToolResult` (built from the internal `run()`'s dict envelope
  # via `_to_call_tool_result`) so FastMCP passes it through unchanged
  # instead of double-wrapping. The `run()` functions themselves keep
  # returning the dict envelope so existing per-tool unit tests
  # (which call `run()` directly) stay stable.
  @server.tool(
    name=read_note_catalog.TOOL_NAME,
    description=read_note_catalog.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_read_note_catalog(
    ctx: Context,
    domain: str | None = None,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    result = await read_note_catalog.run(
      arguments={"domain": domain} if domain is not None else {},
      bearer=bearer,
    )
    return _to_call_tool_result(result)

  @server.tool(
    name=compile_recipe.TOOL_NAME,
    description=compile_recipe.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_compile_recipe(
    ctx: Context,
    source: str,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    result = await compile_recipe.run(
      arguments={"source": source},
      bearer=bearer,
    )
    return _to_call_tool_result(result)

  @server.tool(
    name=run_recipe.TOOL_NAME,
    description=run_recipe.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_run_recipe(
    ctx: Context,
    source: str,
    domains: list[str] | None = None,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    args: dict[str, Any] = {"source": source}
    if domains is not None:
      args["domains"] = domains
    result = await run_recipe.run(arguments=args, bearer=bearer)
    return _to_call_tool_result(result)

  @server.tool(
    name=get_run_result.TOOL_NAME,
    description=get_run_result.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_get_run_result(
    ctx: Context,
    run_id: str,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    result = await get_run_result.run(arguments={"run_id": run_id}, bearer=bearer)
    return _to_call_tool_result(result)

  # Drain CW-MCP-2-E — LOCAL VaultFS-backed handler (no forge-transpile
  # HTTP roundtrip). Bearer still extracted so mis-configured clients
  # fail loudly at the same layer as every other tool; not threaded
  # downstream because the local read has no upstream service to hit.
  # CW-MCP-multi-vault-create-dir — `vault` param routes through the
  # registry.
  @server.tool(
    name=read_notes_in_vault.TOOL_NAME,
    description=read_notes_in_vault.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_read_notes_in_vault(
    ctx: Context,
    filter: str | None = None,
    vault: str | None = None,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    args: dict[str, Any] = {}
    if filter is not None:
      args["filter"] = filter
    if vault is not None:
      args["vault"] = vault
    result = await read_notes_in_vault.run(
      arguments=args,
      bearer=bearer,
      vault_registry=registry,
    )
    return _to_call_tool_result(result)

  # Drain CW-MCP-2-C — commit_recipe writes to the LOCAL vault fs
  # directly (Option C — see drain §Architecture Question). Bearer is
  # still extracted so a mis-configured client can't invoke commit; the
  # bearer is threaded to the internal `forge_run_recipe` call that
  # produces artifacts.
  @server.tool(
    name=commit_recipe.TOOL_NAME,
    description=commit_recipe.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_commit_recipe(
    ctx: Context,
    source: str,
    note_id: str,
    expected_version: int | None = None,
    vault: str | None = None,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    args: dict[str, Any] = {"source": source, "note_id": note_id}
    if expected_version is not None:
      args["expected_version"] = expected_version
    if vault is not None:
      args["vault"] = vault
    result = await commit_recipe.run(
      arguments=args,
      bearer=bearer,
      vault_registry=registry,
    )
    return _to_call_tool_result(result)

  # ---------------------------------------------------------------------------
  # Multi-vault + create tools (CW-MCP-multi-vault-create-dir)
  # ---------------------------------------------------------------------------

  @server.tool(
    name=list_vaults.TOOL_NAME,
    description=list_vaults.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_list_vaults(ctx: Context) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    result = await list_vaults.run(
      arguments={}, bearer=bearer, vault_registry=registry,
    )
    return _to_call_tool_result(result)

  @server.tool(
    name=create_directory.TOOL_NAME,
    description=create_directory.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_create_directory(
    ctx: Context,
    path: str,
    vault: str | None = None,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    args: dict[str, Any] = {"path": path}
    if vault is not None:
      args["vault"] = vault
    result = await create_directory.run(
      arguments=args, bearer=bearer, vault_registry=registry,
    )
    return _to_call_tool_result(result)

  @server.tool(
    name=create_note.TOOL_NAME,
    description=create_note.DESCRIPTION,
    structured_output=True,
  )
  async def _forge_create_note(
    ctx: Context,
    note_id: str,
    description: str | None = None,
    vault: str | None = None,
  ) -> CallToolResult:
    try:
      bearer = _bearer_from_context(ctx)
    except BearerExtractionError as exc:
      return _to_call_tool_result(auth_error_to_tool_result(exc))
    args: dict[str, Any] = {"note_id": note_id}
    if description is not None:
      args["description"] = description
    if vault is not None:
      args["vault"] = vault
    result = await create_note.run(
      arguments=args, bearer=bearer, vault_registry=registry,
    )
    return _to_call_tool_result(result)

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

  # ---------------------------------------------------------------------------
  # Resources — forge-recipe:///{note_id}/v{n}   (CW-MCP-2-C)
  # ---------------------------------------------------------------------------
  #
  # Versioned Recipe artifact — reads the Recipe body as it was at git
  # commit vN of the note. Requires the vault to be git-tracked; when
  # it isn't (drain §6 out-of-scope), returns a "history unavailable"
  # text so the agent knows why the version isn't available rather than
  # seeing a protocol 404.
  #
  # No Bearer required — reads local vault git history, same trust
  # boundary as the forge-mcp process itself.

  @server.resource(
    "forge-recipe:///{note_id}/v{version}",
    name="forge-recipe",
    description=(
      "Versioned Recipe body at commit v{n} of the note. Requires a "
      "git-tracked vault; returns 'history unavailable' text otherwise."
    ),
    mime_type="text/plain",
  )
  async def _read_forge_recipe(note_id: str, version: str) -> str:
    uri = f"forge-recipe:///{note_id}/v{version}"
    # Vault path is env-driven (defaults to bluh). Fresh construction
    # per request so `FORGE_VAULT_PATH` changes take effect without a
    # server restart during smoke.
    try:
      vault_fs = VaultFS(root=Path(os.environ.get("FORGE_VAULT_PATH", "~/forge-vaults/bluh")).expanduser())
    except VaultFSError as exc:
      payload = {
        "contents": [
          {"uri": uri, "mimeType": "text/plain", "text": f"Vault unavailable: {exc}"}
        ]
      }
    else:
      payload = read_recipe_resource(uri=uri, vault_fs=vault_fs)
    contents = payload.get("contents", [])
    if contents and "text" in contents[0]:
      return contents[0]["text"]
    return "{}"

  return server


def main() -> None:
  # Drain CW-MCP-cc-integration — support both transports so the same
  # `forge-mcp` entry point works for Claude Code (stdio subprocess)
  # AND the pre-existing Docker/systemd deployment (streamable-http).
  # `FORGE_MCP_TRANSPORT` picks; default is streamable-http for
  # back-compat with every install path that predates this drain.
  transport = os.environ.get("FORGE_MCP_TRANSPORT", "streamable-http").strip().lower()
  if transport not in ("stdio", "streamable-http", "sse"):
    raise SystemExit(
      f"FORGE_MCP_TRANSPORT={transport!r} is not one of "
      "{'stdio', 'streamable-http', 'sse'}. Use 'stdio' for Claude Code "
      "subprocess installs, 'streamable-http' for hosted deployments."
    )

  # Claude Code spawns a stdio subprocess and speaks JSON-RPC over
  # stdin/stdout — any stray logging on stdout corrupts the protocol
  # stream. Force logs to stderr in stdio mode.
  log_stream = sys.stderr if transport == "stdio" else None
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=log_stream,
  )
  log.info("Starting forge-mcp v%s (transport=%s)", __version__, transport)
  server = _make_server()
  # mypy: FastMCP.run has a Literal-typed transport param, but the
  # `if transport not in (...)` guard above already restricts it to
  # exactly that literal set.
  server.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
  main()
