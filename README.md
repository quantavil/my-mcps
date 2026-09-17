# my-mcps

Centralized Model Context Protocol (MCP) server manager powered by `bunx`.

It merges servers declared in `mcp-servers.json` into your local AI agent configs without overwriting user-defined servers.

### Supported Agents
- **Antigravity CLI** (`~/.gemini/antigravity-cli/mcp_config.json`)
- **Codex CLI** (`~/.codex/config.toml`)
- **Claude Desktop** (`~/.config/Claude/claude_desktop_config.json`)
- **Claude Code** (`~/.claude.json`)
- **Cursor** (`~/.cursor/mcp.json`)
- **OpenCode** (`~/.config/opencode/opencode.json`)

### Usage

```bash
# 1. (Optional) Set secrets in .env
cp .env.example .env

# 2. Deploy to all agent configs
./run.sh deploy

# 3. List active servers
./run.sh list

# 4. Verify secrets coverage
./run.sh check
```
