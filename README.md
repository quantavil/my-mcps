# my-mcps

Centralized Model Context Protocol (MCP) server manager powered by `bunx`.

It merges servers declared in `mcp-servers.json` into your local AI agent configs without overwriting user-defined servers.

The four required Ditto evidence servers and Dart development MCP
are documented in [docs/ditto-mcp-integration.md](docs/ditto-mcp-integration.md).
Their configuration alone does not prove a capability; run real APK and emulator
probes before using a Ditto receipt.

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

# 7. Check upstream synchronization status
./run.sh sync

# 8. Show help
./run.sh help
```

### Environment & Variable Interpolation

- Server arguments and environment definitions in `mcp-servers.json` support `${VAR}` syntax.
- `${HOME}` expands to the current user's home directory.
- All other `${SECRET_NAME}` references are interpolated from `.env` (or inherited from the shell environment).
- Missing secrets are safely omitted from deployed agent configs without leaking raw template placeholders.
