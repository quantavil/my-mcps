# my-mcps

One manifest (`mcp-servers.json`) for the MCP servers of every local AI agent. `deploy` merges them into each agent's config as `managed-*` entries and leaves your own entries alone.

Agents: Claude Code (`~/.claude.json`), Claude Desktop (`~/.config/Claude/claude_desktop_config.json`), Codex (`~/.codex/config.toml`), OpenCode (`~/.config/opencode/opencode.json`), Cursor (`~/.cursor/mcp.json`), Antigravity CLI (`~/.gemini/config/mcp_config.json`).

## Usage

```bash
cp .env.example .env                # fill in API keys
./run.sh deploy                     # deploy to all agents, cache new gateway tool lists (default command)
./run.sh list                       # servers with tier, status and secrets
./run.sh enable|disable <name>      # toggle in the manifest and redeploy
./run.sh index [--refresh] [names]  # (re)cache gateway tool lists
./run.sh search "apk signature"     # what an agent's search_tools would return
./run.sh check                      # tiers valid, every ${SECRET} documented in .env.example
./run.sh test
```

## Tiers

Every server sets `"tier"`:

- **`core`**: written into every agent config, so its tools are always loaded. Only for servers used in most sessions (firecrawl, context7).
- **`gateway`**: hidden behind one `managed-gateway` server with two tools:
  - `search_tools(query, server?, limit?)`: BM25 search over all hidden tools, returning names, descriptions and input schemas.
  - `call_tool(server, tool, arguments)`: runs one.

  The list of hidden servers and what each is for is sent as both the server instructions and the `search_tools` description, so the agent knows when to search.

Gateway servers can be local (`command`/`args`) or remote (`url`, optional `headers`). Tool lists are cached in `~/.cache/my-mcps/tools/`, so searching doesn't start servers. A server starts on its first `call_tool`, refreshes its cached tool list, and stays up for the session; it is restarted if it exits. A server that fails to start is skipped by later searches unless one names it. Gateway secrets stay in this repo and never go into agent configs. Each child process gets the agent's environment plus its own `env`, not the rest of `.env`.

## Versions

Nothing is pinned. `bunx`/`uvx` servers use `pkg@latest`, so every fresh start runs the newest release; `uvx` needs the explicit `@latest`, otherwise it reuses its cached copy. Because the tool list refreshes on first call, upgrades reach search with no manual step.

## Secrets

`${VAR}` in `command`, `args`, `env`, `url` and `headers` resolves from `.env`, then the shell environment. `${HOME}` is your home directory. An env var with a missing secret is left out of the deployed config rather than written as a literal placeholder.

## Notes

Codex stops tool calls after 60s by default, so deploy sets `tool_timeout_sec` on its gateway entry to fit slow calls such as rea analyses.
