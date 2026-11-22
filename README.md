# my-mcps

One manifest (`mcp-servers.json`) for the MCP servers of every local AI agent. `deploy` merges them directly into each agent's config as `managed-*` entries and leaves your own entries alone.

Agents supported: Claude Code (`~/.claude.json`), Claude Desktop (`~/.config/Claude/claude_desktop_config.json`), Codex (`~/.codex/config.toml`), OpenCode (`~/.config/opencode/opencode.json`), Cursor (`~/.cursor/mcp.json`), Antigravity CLI (`~/.gemini/config/mcp_config.json`).

## Usage

```bash
cp .env.example .env            # fill in API keys
./run.sh deploy                 # deploy all enabled servers to all agents (default command)
./run.sh list                   # list servers with status and secrets
./run.sh enable|disable <name>  # toggle server in manifest and redeploy
./run.sh check                  # validate server definitions and .env.example
./run.sh test                   # run test suite
```

## Servers

Servers are defined in `mcp-servers.json`:
- **Local servers**: specify `command`, `args`, and optional `env`.
- **Remote servers**: specify `url` and optional `headers`.
- Toggle with `"enabled": false` (or via `./run.sh disable <name>`).

## Secrets

`${VAR}` in `command`, `args`, `env`, `url`, and `headers` resolves from `.env`, then the shell environment. `${HOME}` is your home directory. An env var with a missing secret is left out of the deployed config rather than written as a literal placeholder.
