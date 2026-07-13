# forge-mcp — install + configure

Two paths: hosted (`mcp.forge.example`) or self-hosted (Docker + your own
domain, or bare-metal via the systemd unit). Both surface the same
Streamable HTTP transport; only the endpoint URL + your Bearer token
change.

## Prerequisites

- A **forge-transpile Bearer token** — CW-MCP-1-B does not validate
  tokens itself; forge-transpile is the source of truth. Grab yours
  from bluh's plugin settings:

  ```bash
  jq -r '.transpileServiceToken' \
    ~/forge-vaults/bluh/.obsidian/plugins/forge-client-obsidian/data.json
  ```

  Rotate this string on the forge-transpile side (see forge-transpile's
  auth.py) whenever you want to revoke access. The token is the ONLY
  credential forge-mcp forwards.

- **A Claude Desktop install** (or any MCP client that speaks Streamable
  HTTP). This doc uses Claude Desktop as the canonical example.

## Path A — hosted `mcp.forge.example`

Add this block to your Claude Desktop config (`~/Library/Application
Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "forge-mcp": {
      "url": "https://mcp.forge.example/mcp",
      "headers": {
        "Authorization": "Bearer <paste-your-forge-transpile-token>"
      }
    }
  }
}
```

Restart Claude Desktop. Ask "list music library notes." Expect a
`forge_read_note_catalog` tool call with ~35 chips including
`voices_canonical`.

## Path B — self-hosted (Docker)

```bash
docker build -t forge-mcp:latest .

# Point FORGE_TRANSPILE_URL at your forge-transpile deployment.
docker run --rm -p 8765:8765 \
    -e FORGE_TRANSPILE_URL=https://forge.thecodingarena.com \
    forge-mcp:latest
```

Your Claude Desktop config points at `http://localhost:8765/mcp`:

```json
{
  "mcpServers": {
    "forge-mcp": {
      "url": "http://localhost:8765/mcp",
      "headers": {
        "Authorization": "Bearer <paste-your-forge-transpile-token>"
      }
    }
  }
}
```

## Path C — self-hosted (systemd)

```bash
git clone https://github.com/frmoded/forge-mcp /opt/forge-mcp
cd /opt/forge-mcp
python3 -m venv venv
venv/bin/pip install -e .
sudo cp deploy/mcp-forge.service /etc/systemd/system/
sudo useradd --system --home /opt/forge-mcp --shell /usr/sbin/nologin forgemcp
sudo chown -R forgemcp:forgemcp /opt/forge-mcp
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-forge
```

Point nginx (or your reverse proxy) at `127.0.0.1:8765` — the shipped
`deploy/mcp.forge.example.nginx.conf` is a working template.

## Verifying the install

Quick health check:

```bash
curl -s http://localhost:8765/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Accept: application/json" \
  -X POST -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Expect a JSON-RPC `initialize` response naming forge-mcp + version.

## Rotating tokens

Because forge-mcp does NOT cache or validate tokens itself, rotation is
zero-downtime on the forge-mcp side:

1. Update forge-transpile's `FORGE_TRANSPILE_SECRET`.
2. Update your MCP client's Authorization header to the new value.
3. Old tokens fail with `isError: true, "forge-transpile rejected the
   Bearer token"` on the next request — that's the client's signal to
   rotate.

## Troubleshooting

- **`AUTH_MISSING` in every tool call** — your MCP client isn't sending
  Authorization. Verify the config file syntax; some clients require a
  full-form JSON with `"transport": {"type": "http", ...}`.
- **`invalid token` (401) despite a valid-looking Bearer** — the token
  in bluh's `data.json` might be a stale local copy; rotate on
  forge-transpile and grab the new one from bluh (or set
  `FORGE_TRANSPILE_SECRET` on forge-transpile to the same string your
  client sends).
- **`endpoint has not been implemented yet`** — forge-transpile is
  missing `/catalog` or `/vault/notes`. This is tracked in CW-MCP-1-A
  FEEDBACK §L47; drain 1330 shipped `/catalog` so hosted forge-transpile
  should not hit this. Local dev on an older forge-transpile will.
