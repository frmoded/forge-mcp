# forge-mcp

**forge-MCP exposes the Forge E-- library note catalog and vault as an
MCP server, so any MCP-consuming agent (Claude Desktop, Cursor, …) can
enumerate the catalog and read library notes by URI.** This repo ships
Sprint 1 (read-only surface): the two catalog/vault tools and the
`forge-note:///` resource scheme. Compile / run / commit tools land in
Sprint 2.

## Install

**Full walkthrough with Docker + systemd + nginx TLS**:
[docs/install.md](docs/install.md).

Quick paths:

```bash
# Local dev (pip)
pip install -e ".[dev]"
python -m forge_mcp.server

# Docker
docker build -t forge-mcp:latest .
docker run --rm -p 8765:8765 \
    -e FORGE_TRANSPILE_URL=https://forge.thecodingarena.com \
    forge-mcp:latest
```

Environment:

- `FORGE_TRANSPILE_URL` — base URL of the forge-transpile service.
  Default: `http://localhost:8000`.
- `FORGE_MCP_HOST` — host to bind. Default: `0.0.0.0`.
- `FORGE_MCP_PORT` — port to bind. Default: `8765`.
- `FORGE_MCP_BEARER` — **dev fallback only**. Per-request Bearer
  extraction is the primary path (CW-MCP-1-B); this env var only
  fires when the incoming request has no `Authorization` header.
  Do NOT set in production.

## Auth

forge-mcp does NOT validate tokens itself — forge-transpile is the
source of truth (guarded by `FORGE_TRANSPILE_SECRET`). Each request's
`Authorization: Bearer <token>` header is forwarded verbatim; a 401 or
403 from forge-transpile surfaces as `isError: true` with an actionable
message the agent can read.

Rotation is zero-downtime on the forge-mcp side: change
`FORGE_TRANSPILE_SECRET` on forge-transpile, update your MCP client's
header, done. Old tokens fail on the next request with a clean
rejection message.

## Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(hosted example — self-host swaps the URL for
`http://localhost:8765/mcp`):

```json
{
  "mcpServers": {
    "forge-mcp": {
      "url": "https://mcp.forge.example/mcp",
      "headers": {
        "Authorization": "Bearer <your-forge-transpile-token>"
      }
    }
  }
}
```

Grab your token with:

```bash
jq -r '.transpileServiceToken' \
    ~/forge-vaults/bluh/.obsidian/plugins/forge-client-obsidian/data.json
```

## Tools

- `forge_read_note_catalog({domain?})` — list library notes.
- `forge_read_notes_in_vault({filter?})` — list vault notes.

## Resources

- `forge-note:///{domain}/{name}` — stable identifier for a library note.

## Landing page

Full user-facing docs will live at `TBD` (see CW-MCP-3-B).
