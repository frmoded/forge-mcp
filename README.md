# forge-mcp

**Author, compile, run, and commit generative-music E-- Recipes directly from any MCP-capable agent (Claude Desktop, Cursor, …).** forge-mcp exposes the Forge E-- library note catalog + vault as an MCP server and closes the authoring loop end-to-end: the agent picks a chip from the catalog, drafts a Recipe, verifies it parses, runs it in a sandbox, previews the artifact, and commits the finished Recipe to a vault note. All 6 tools ship today.

```
[library note catalog] → [compile] → [run] → [commit] → [vault note with recipe_version bump]
```

## Install into Claude Code

**Fastest path** — Claude Code spawns forge-mcp as a stdio subprocess. Full walkthrough at [docs/claude-code-install.md](docs/claude-code-install.md).

```bash
# 1. Install (once repo is public — see FEEDBACK §Driver ops)
pip install "git+https://github.com/frmoded/forge-mcp.git"

# 2. Fetch your Bearer once and export
export FORGE_MCP_BEARER=$(jq -r '.transpileServiceToken' \
  ~/forge-vaults/bluh/.obsidian/plugins/forge-client-obsidian/data.json)

# 3. Register with Claude Code
claude mcp add forge-mcp \
  -e FORGE_MCP_BEARER=$FORGE_MCP_BEARER \
  -e FORGE_TRANSPILE_URL=https://forge.thecodingarena.com \
  -e FORGE_VAULT_PATH=$HOME/forge-vaults/bluh \
  -e FORGE_MCP_TRANSPORT=stdio \
  -- forge-mcp

# 4. Start Claude Code and ask "list the notes in my forge music library."
```

## Install (other clients)

Full walkthrough (Claude Desktop config, forge-transpile Bearer acquisition, verification smoke, troubleshooting): [docs/install.md](docs/install.md).

Quick paths:

```bash
# From source (pip + editable install for development)
pip install -e ".[dev]"
python -m forge_mcp.server

# Docker
docker build -t forge-mcp:latest .
docker run --rm -p 8765:8765 \
    -e FORGE_TRANSPILE_URL=https://forge.thecodingarena.com \
    -e FORGE_VAULT_PATH=/path/to/your/vault \
    forge-mcp:latest
```

Environment:

- `FORGE_TRANSPILE_URL` — base URL of the forge-transpile service. Default: `http://localhost:8000`.
- `FORGE_VAULT_PATH` — local vault directory for `forge_read_notes_in_vault` + `forge_commit_recipe`. Default: `~/forge-vaults/bluh`.
- `FORGE_MCP_HOST` — host to bind. Default: `0.0.0.0`.
- `FORGE_MCP_PORT` — port to bind. Default: `8765`.
- `FORGE_MCP_BEARER` — **dev fallback only**. Per-request Bearer extraction is the primary path (CW-MCP-1-B); this env var only fires when the incoming request has no `Authorization` header. Do NOT set in production.

## Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "forge-mcp": {
      "url": "http://localhost:8765/mcp",
      "headers": {
        "Authorization": "Bearer <your-forge-transpile-token>"
      }
    }
  }
}
```

Get your Bearer:

```bash
jq -r '.transpileServiceToken' \
    ~/forge-vaults/bluh/.obsidian/plugins/forge-client-obsidian/data.json
```

## Tools

**Read** (no side effects):

- `forge_read_note_catalog({domain?})` — list Forge library notes; every entry carries the E-- signature the agent needs to `Call` it.
- `forge_read_notes_in_vault({filter?})` — list vault notes with a `has_recipe` + `recipe_version` summary. Backed by a local filesystem walk (CW-MCP-2-E).

**Author** (deterministic — no LLM, no vault write):

- `forge_compile_recipe({source})` — Recipe → Python. Returns compiled source + unresolved slot count, OR a structured parse error with line/column (per drain CW-recipe-parser-line-info).
- `forge_run_recipe({source, domains?})` — compile + execute in a resource-limited server sandbox. Returns a short preview + a `run_id`; artifacts (MusicXML / MIDI / PNGs) accessible via the `forge-artifact://` resource.
- `forge_get_run_result({run_id})` — fetch full stdout/stderr + artifact manifest of a previous run. 7-day TTL, per-Bearer isolation.

**Commit**:

- `forge_commit_recipe({source, note_id, expected_version?})` — persist Recipe to a vault note (facet-scoped — Description + Python + frontmatter survive byte-for-byte). Bumps `recipe_version` in the note's frontmatter. Optimistic-concurrency via `expected_version`; version-conflict returns `isError:true` with expected + current numbers.

## Resources

- `forge-note:///{domain}/{name}` — library note content.
- `forge-artifact:///{run_id}/{artifact_name}` — on-demand binary fetch for run artifacts. Text mimes return via `text`; binaries via base64 `blob`.
- `forge-recipe:///{note_id}/v{n}` — Recipe body at a specific `recipe_version` (git-tracked vaults only; returns "history unavailable" text otherwise).

## Auth

forge-mcp does NOT validate tokens itself — forge-transpile is the source of truth (guarded by `FORGE_TRANSPILE_SECRET`). Each request's `Authorization: Bearer <token>` header is forwarded verbatim; a 401 or 403 from forge-transpile surfaces as `isError: true` with an actionable message the agent can read (drain CW-MCP-1-B).

Rotation is zero-downtime on the forge-mcp side: change `FORGE_TRANSPILE_SECRET` on forge-transpile, update your MCP client's header, done. Old tokens fail on the next request with a clean rejection message.

## Related repos

- **[forge](https://github.com/frmoded/forge)** — the E-- parser + transpiler + core music library. forge-mcp vendors a snapshot of `forge/recipe/` per the CW-MCP-2-A architecture; drift is caught by `scripts/check-recipe-drift.sh` in the forge-transpile repo.
- **[forge-transpile](https://github.com/frmoded/forge-transpile)** — the FastAPI service exposing `/compile` / `/run` / `/catalog` etc. that forge-mcp's tools proxy for the transpile + sandboxed-run paths. Vault reads + commits are LOCAL and don't hit forge-transpile.
- **forge-client-obsidian** — the Obsidian plugin end of the same authoring loop. forge-mcp writes to the SAME vault the plugin reads/renders; both share the note-file format.
