# forge-mcp — install into Claude Code

**Priority-1 install path** (drain CW-MCP-cc-integration). Get forge-mcp working in your Claude Code CLI in under two minutes.

Two transports:
- **stdio** (RECOMMENDED for Claude Code) — Claude Code spawns `forge-mcp` as a subprocess. No separate server; env vars supplied at add-time.
- **http** — you run `forge-mcp` in a terminal on port 8765, Claude Code speaks to it over the Streamable HTTP transport. Useful if multiple clients share one server.

## Prerequisites

- Python 3.12+ (3.11 works but 3.12 matches this repo's mypy target).
- Claude Code installed (`claude --version` prints something).
- A forge-transpile Bearer token — fetch it once and stash it:
  ```bash
  export FORGE_MCP_BEARER=$(jq -r '.transpileServiceToken' \
    ~/forge-vaults/bluh/.obsidian/plugins/forge-client-obsidian/data.json)
  ```
- A vault directory (`FORGE_VAULT_PATH`) — the same one your Obsidian plugin uses. Default `~/forge-vaults/bluh`.

## Install the package

Pick one:

```bash
# From git (after driver runs §Driver ops in the FEEDBACK — repo public + pushed)
pip install "git+https://github.com/<your-handle>/forge-mcp.git"

# From a local checkout (developer mode; picks up your local edits)
git clone https://github.com/<your-handle>/forge-mcp.git ~/projects/forge-mcp
pip install -e ~/projects/forge-mcp
```

Verify the entry point is on PATH:

```bash
which forge-mcp
# → ~/.venv/bin/forge-mcp (or similar)
```

## Path A — stdio (recommended)

```bash
claude mcp add forge-mcp \
  -e FORGE_MCP_BEARER=$FORGE_MCP_BEARER \
  -e FORGE_TRANSPILE_URL=https://forge.thecodingarena.com \
  -e FORGE_VAULT_PATH=$HOME/forge-vaults/bluh \
  -e FORGE_MCP_TRANSPORT=stdio \
  -- forge-mcp
```

That's it. Start a Claude Code session (`claude`) and ask:

> "List the notes in my forge music library."

Expected: Claude calls `forge_read_note_catalog(domain="music")` and prints ~35 notes.

## Path B — Streamable HTTP (multi-client, shared server)

Terminal 1 (leave running):

```bash
export FORGE_MCP_BEARER=...
export FORGE_TRANSPILE_URL=https://forge.thecodingarena.com
export FORGE_VAULT_PATH=$HOME/forge-vaults/bluh
export FORGE_MCP_TRANSPORT=streamable-http
forge-mcp
# → 2026-07-14 17:20:00 INFO forge-mcp: Starting forge-mcp v0.1.0 (transport=streamable-http)
# → listening on 0.0.0.0:8765
```

Terminal 2:

```bash
claude mcp add forge-mcp \
  --transport http \
  http://localhost:8765/mcp \
  --header "Authorization: Bearer $FORGE_MCP_BEARER"
```

Same verification prompt as Path A.

## Verifying end-to-end (driver smoke)

Once `claude mcp list` shows `forge-mcp: connected`, drive the full authoring loop from a Claude Code session:

**Read the catalog**:
> "List the music library notes."

Expected: `forge_read_note_catalog(domain="music")` call, 35+ notes with `name` / `short_desc`.

**Compose + commit a trivial recipe**:
> "Commit a Recipe that returns 42 to my mcp-scratch/cc-test note."

Expected: agent calls `forge_compile_recipe`, then `forge_commit_recipe`, returns `{note_id, committed_version, run_id}`. Check the vault:

```bash
cat ~/forge-vaults/bluh/mcp-scratch/cc-test.md
# → frontmatter has `recipe_version: 1`
# → `# Recipe\n\nReturn 42.\n` block present
```

## Troubleshooting

- **`claude mcp list` shows forge-mcp as "failed"** — check `claude mcp get forge-mcp` for the exact error. Most common:
  - `forge-mcp: command not found` → the pip install went to a venv Claude Code can't reach. Use the absolute path: `-- /full/path/to/forge-mcp` in `claude mcp add`.
  - `Missing FORGE_MCP_BEARER` → the `-e` flags didn't take. Re-run `claude mcp remove forge-mcp && claude mcp add ...` with a fresh terminal that has the env vars.

- **Tool call returns `"forge-transpile rejected the Bearer token (HTTP 401 invalid token). Rotate FORGE_MCP_BEARER..."`** — your token is stale. Refresh from the plugin's `data.json` (see Prerequisites) and re-add the MCP server.

- **`forge_commit_recipe` returns `"Vault filesystem unavailable"`** — `FORGE_VAULT_PATH` points at a directory that doesn't exist. Create it or point at a real vault. Default is `~/forge-vaults/bluh`.

- **Port 8765 already in use** (Path B only) — something else is bound. Free it or change the port:
  ```bash
  lsof -ti:8765 | xargs kill                # kill the offender
  # OR
  export FORGE_MCP_PORT=8766                # use a different port + update the claude mcp add URL
  ```

- **Recipe parse errors — agent doesn't see line/column** — you're on a pre-drain-CW-recipe-parser-line-info forge-transpile. The tool text WILL still name the message, but structured location won't populate. Redeploy forge-transpile.

- **`claude mcp add` complains "transport not one of {stdio,sse,http}"** — you're on an older Claude Code that predates Streamable HTTP support. Upgrade Claude Code, or use stdio (Path A works everywhere `claude mcp add` exists).

## Removing forge-mcp

```bash
claude mcp remove forge-mcp
pip uninstall forge-mcp     # optional; leaves the package installed by default
```

## Alternative install paths

If you use Claude Desktop (the GUI app), stdio and Streamable HTTP are both supported via `~/Library/Application Support/Claude/claude_desktop_config.json`. See [install.md](./install.md).
