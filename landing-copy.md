# forge-mcp — Registry landing copy

One-page copy for the Anthropic MCP Registry listing + adjacent surfaces (README hero, Product Hunt, HN Show HN, blog post). Kept synthetic — no cohort-member content, no unshipped features promised, no personal vault paths in screenshots.

---

## Hero (one sentence)

**Author, run, and commit generative-music E-- Recipes end-to-end from any MCP-capable agent.**

## What it does (paragraph)

forge-mcp exposes the Forge music-composition surface as an MCP server. Your agent (Claude Desktop, Cursor, or any MCP-capable client) can browse the library of composition primitives, draft a Recipe in the small E-- DSL, verify it parses with structured line/column errors, run it in a resource-limited sandbox to produce MusicXML / MIDI / PNG artifacts, and — when it likes what it hears — commit the Recipe to a vault note that Obsidian re-renders on the driver's disk. Six tools, three resource schemes, one closed authoring loop.

## Who it's for

- **Music-adjacent hackers** who want an LLM to compose 12-bar blues progressions, walking basslines, or moda-domain rhythmic sequences without leaving their chat.
- **Forge cohort members** whose bluh vault already lives locally — forge-mcp is a first-class way to author into that vault from an MCP client instead of Obsidian.
- **MCP tool-builders** who want a worked example of stateful, resource-scoped MCP: real file writes, real sandbox execution, real optimistic-concurrency conflict handling.

## Install (30 seconds)

```bash
pip install forge-mcp
```

Then add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "forge-mcp": {
      "url": "http://localhost:8765/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

Grab the token from your Forge Obsidian plugin settings (documented at `docs/install.md` in the repo).

## 30-second demo storyline

1. **Ask**: "Compose a slow, sparse blues in F minor for kick + walking bass."
2. **Agent**: `forge_read_note_catalog(domain="music")` → picks `walking_bass_line` + `play_at_beats` chips from the response.
3. **Agent**: `forge_compile_recipe(source="Let bass = Call [[walking_bass_line]] with harmony=..., tempo=68. Return bass.")` → gets Python back.
4. **Agent**: `forge_run_recipe(source=...)` → run_id + a MusicXML preview.
5. **Agent**: renders the artifact via `forge-artifact:///<run_id>/score.musicxml` — plays it back.
6. **Agent**: `forge_commit_recipe(source=..., note_id="composition/slow_burn_in_f")` → recipe lands in your Obsidian vault at `~/forge-vaults/bluh/composition/slow_burn_in_f.md`. Version 1. Re-run any time.

Time from prompt to committed Recipe: about 5 seconds.

## What makes it useful for MCP-tool-builders

- **Real filesystem writes, path-safe.** `forge_commit_recipe` writes to a local vault via a hardened path-traversal defense (3-layer: regex reject, `Path.resolve()`, `.relative_to(root)`). Reference implementation for MCP tools that need to touch the user's disk without exposing `..` escapes.
- **Optimistic concurrency, agent-driven.** Every commit takes an `expected_version`; a stale write returns a structured `isError:true` naming both the expected and current versions. No merge magic; the agent re-fetches and retries. Reference implementation for D-mcp-3 in the tool-surface v1 spec.
- **Structured errors, three-part shape.** Every isError message follows "what went wrong / what was expected / one-line example that would work" where the third part is meaningful. Regression-locked with parametrized tests. See `tests/test_error_message_shape.py`.
- **Wire-shape-clean.** `structuredContent` is the flat outputSchema payload (drain CW-MCP-fastmcp-doublewrap). Every tool's schema is meta-validated at test time (`tests/test_output_schemas_audit.py`).
- **Sandbox isolation** (via the paired forge-transpile service): AST allowlist pre-check, subprocess rlimits, cwd-scoped artifact discovery, per-Bearer run-store isolation via `sha256(bearer)[:32]`.

## What it explicitly does NOT do (yet)

- **Multi-user vault isolation.** One vault per forge-mcp instance today; the `FORGE_VAULT_PATH` env var configures which. Sprint 3+ material.
- **Hosted `mcp.forge.example`.** Local install only for launch. The forge-transpile service IS hosted (public EC2), but the vault write requires local fs — which means running forge-mcp locally too.
- **OAuth 2.1 dynamic client registration.** Bearer-header auth today; OAuth flow deferred to a future MCP protocol tick.
- **Non-music domains beyond `music` + `moda`.** New domains are additive; add a `<domain>_lib.py` to the forge engine and forge-mcp picks it up on next deploy.

## Links

- Repo: https://github.com/frmoded/forge-mcp
- Install docs: [docs/install.md](docs/install.md)
- PyPI: https://pypi.org/project/forge-mcp/ (publication pending)
- Tool-surface spec: `forge-mcp-tool-surface-v1.md` in the forge-moda-bootstrap repo
- Related: [forge-transpile](https://github.com/frmoded/forge-transpile), [forge](https://github.com/frmoded/forge)
