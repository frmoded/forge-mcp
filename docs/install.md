# forge-mcp — install + configure

Three paths, in order of Sprint 3 maturity:

- **Path 0 — PyPI + Claude Desktop** (RECOMMENDED): `pip install forge-recipe-mcp`, add a Claude Desktop stanza. Local Streamable HTTP transport. This is the flow the MCP Registry listing points at (drain CW-MCP-3-B).
- **Path A — hosted `mcp.forge.example`** (Sprint 3+ hardening): remote Streamable HTTP; not shipped for launch (see [landing-copy.md](../landing-copy.md) "What it explicitly does NOT do").
- **Path B — self-hosted Docker + nginx + systemd**: for cohort members who want to run the same shape as production locally.

## Path 0 — PyPI install (fastest)

```bash
pip install forge-recipe-mcp
```

Package page: https://pypi.org/project/forge-recipe-mcp/. The PyPI distribution name is `forge-recipe-mcp` because `forge-mcp` was taken by an unrelated project (see drain CW-MCP-pypi-license-400). The installed CLI command is still `forge-mcp` — no downstream config change.

Configure Claude Desktop by adding to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "forge-mcp": {
      "command": "forge-mcp",
      "env": {
        "FORGE_TRANSPILE_URL": "https://forge.thecodingarena.com",
        "FORGE_VAULT_PATH": "~/forge-vaults/bluh",
        "FORGE_MCP_BEARER": "<paste-your-token>"
      }
    }
  }
}
```

Verify (restart Claude Desktop, open a chat, ask):

> "List the music library notes available via forge-mcp."

Expected: Claude calls `forge_read_note_catalog(domain="music")` and shows ~35 chip names.

### Troubleshooting Path 0

- **"No tools available" in Claude Desktop**: check the client log (`~/Library/Logs/Claude/mcp*.log` on macOS). Most common cause: `forge-mcp` command not on PATH — try `command: /full/path/to/forge-mcp` in the config.
- **"forge-transpile rejected the Bearer token (HTTP 401 ...)"**: the token in your config is stale. Refresh via `jq -r '.transpileServiceToken' ~/forge-vaults/bluh/.obsidian/plugins/forge-client-obsidian/data.json` and re-paste.
- **`forge_commit_recipe` returns "Vault filesystem unavailable"**: check `FORGE_VAULT_PATH` exists + is a directory. Default is `~/forge-vaults/bluh`.
- **`forge_read_note_catalog` returns `"No notes found for domain 'music'"`**: forge-transpile is up but /catalog is empty — the deployed service is missing engine chips. Fixed on the forge-transpile side; ping @driver.
- **Structured content nesting looks weird (`.structuredContent.structuredContent.notes`)**: you're on a pre-CW-MCP-fastmcp-doublewrap build. Upgrade to forge-mcp >= 0.1.0.

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

## Security posture

**Trust model.** You bring your own Bearer token, run forge-mcp locally on your own machine, and read/write your own vault. Nothing in forge-mcp executes code without your Bearer holder's consent. Being listed in the MCP Registry changes discovery — not the trust boundary. Every install is opt-in and local.

**Sandbox layers** (paired forge-transpile service — see [forge-transpile/sandbox.py](https://github.com/frmoded/forge-transpile/blob/main/sandbox.py)):

- **AST-level import allowlist.** Recipe-generated Python is walked with `ast` before execution; imports outside a strict list (`math`, `random`, `music21`, vendored `engine_libs.*`, …) reject before the subprocess spawns. Blocks casual reach into `os`, `sys`, `subprocess`, `socket`, `pathlib`, `urllib`, `requests`, `httpx`.
- **Subprocess isolation.** Each run spawns a fresh Python subprocess with per-process `rlimits`: CPU ≤ 30s, address space ≤ 512 MB, `RLIMIT_NPROC=0` (no fork), file writes ≤ 10 MB. Hard limits on Linux (prod EC2); best-effort per-limit on macOS (some `setrlimit` calls no-op there).
- **Per-run cwd scoping.** Artifact discovery is bounded to `/tmp/forge-artifacts/{run_id}/` — one run can't read another run's files.
- **Per-Bearer isolation of the runs store.** Every stored run is keyed by `sha256(bearer)[:32]`. A leaked `run_id` cannot be fetched with a different Bearer — `GET /run/{id}` returns 404 (not 403) to avoid confirming the id exists.

**Accidental vs adversarial.** The AST allowlist is a real defense against **accidental attacks** — the common case is an LLM generating code that tries `import os` because it's trained on general Python and forgot the domain constraint. It is **not** a hardened boundary against **adversarial code**:

- Determined attacks via `__import__` string manipulation or reflection can bypass the AST check.
- **Container / namespace isolation** (rootless podman, `bwrap`, gVisor, Firecracker) is NOT applied. Subprocess shares the host process + network namespace within the rlimit budget.
- **Network egress** is not blocked at the sandbox level. `music21` can in principle fetch remote XML if code passes it a URL; no known vendored code path does this, but the surface exists.
- **Filesystem reads outside cwd** are possible for anything the sandbox uid can read. Only writes are contained.

If your threat model includes untrusted code producers, do NOT rely on forge-mcp's sandbox alone — run forge-mcp itself inside a container.

**Recommendations for users.**

- Only install forge-mcp from sources you trust: this GitHub repo (`https://github.com/frmoded/forge-mcp`) or its PyPI package.
- Use a distinct Bearer per environment where possible (one token per user today; multi-tenant is Sprint 4+ material).
- Report unexpected sandbox escapes at [github.com/frmoded/forge-mcp/issues](https://github.com/frmoded/forge-mcp/issues).

**What Sprint 4+ may add if the threat model warrants it** (not roadmapped):

- Container isolation for the sandbox subprocess (rootless podman / `bwrap` wrapper). ~50 ms per-run cost.
- Network `unshare -n` in the sandbox's `preexec_fn`. Near-zero cost when isolation is already in play.
- `seccomp` filter dropping `execve`, `ptrace`, `mount`, and other syscalls the sandbox has no business making.

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
