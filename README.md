# my-mcps

Centralized Model Context Protocol (MCP) server manager powered by `bunx`.

It merges servers declared in `mcp-servers.json` into your local AI agent configs without overwriting user-defined servers.

### Supported Agents
- **Antigravity CLI** (`~/.gemini/config/mcp_config.json`)
- **Codex CLI** (`~/.codex/config.toml`)
- **Claude Desktop** (`~/.config/Claude/claude_desktop_config.json`)
- **Claude Code** (`~/.claude.json`)
- **Cursor** (`~/.cursor/mcp.json`)
- **OpenCode** (`~/.config/opencode/opencode.json`)

### Usage

```bash
# 1. (Optional) Set secrets in .env
cp .env.example .env

# 2. Deploy to all agent configs (default)
./run.sh deploy

# 3. List configured servers with enabled/disabled status
./run.sh list

# 4. Enable or disable servers (persists in repo & immediately redeploys)
./run.sh disable <server-name>
./run.sh enable <server-name>

# 5. Verify all referenced secrets are documented in .env.example
./run.sh check

# 6. Run test suite
./run.sh test

# 7. Cache gateway tool lists / try a search
./run.sh index
./run.sh search "flutter hot reload"

# 8. Check upstream synchronization status
./run.sh sync

# 9. Show help
./run.sh help
```

### Tiers: core and gateway

Every server in `mcp-servers.json` sets `"tier"` (`./run.sh check` fails without it):

- **`core`**: deployed straight into every agent config, so its tools are always loaded. Keep this for servers used across most tasks (firecrawl, context7, icegate).
- **`gateway`**: hidden behind one `managed-gateway` server that exposes only two tools:
  - `search_tools(query, server?, limit?)` ranks every hidden tool with BM25 and returns names, descriptions and input schemas. Its description lists each hidden server, so the agent knows when to search.
  - `call_tool(server, tool, arguments)` runs one.

Gateway servers can be local (`command`/`args`) or remote (`url`, optional `headers`, both with `${VAR}` interpolation). Their tool lists are cached in `~/.cache/my-mcps/tools/`, so searching never starts a server; a server starts on its first `call_tool` and stays up for the session. A child process gets the agent's environment plus its own declared `env`, never other `.env` secrets, and gateway secrets are never written into agent configs.

```bash
./run.sh index              # cache tool lists of all gateway servers (after adding one)
./run.sh index --refresh rea
./run.sh search "apk signature"   # see what an agent's search would return
```

Promote a server to `core` only when it is used in most sessions; anything task-specific belongs behind the gateway.

### Environment & Variable Interpolation

- Server arguments and environment definitions in `mcp-servers.json` support `${VAR}` syntax.
- `${HOME}` expands to the current user's home directory.
- All other `${SECRET_NAME}` references are interpolated from `.env` (or inherited from the shell environment).
- Missing secrets are safely omitted from deployed agent configs without leaking raw template placeholders.
