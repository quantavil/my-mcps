# my-mcps

A standalone manager to centrally configure, version-lock, auto-document, and deploy Model Context Protocol (MCP) servers across AI agents:
- **Antigravity CLI** (`~/.gemini/antigravity-cli/mcp_config.json`)
- **Claude Desktop** (`~/.config/Claude/claude_desktop_config.json`)
- **Claude Code** (`~/.claude.json`)
- **Cursor** (`~/.cursor/mcp.json`)
- **OpenCode** (`~/.config/opencode/opencode.json`)

Deployments perform non-destructive merges, prefixing managed servers with `managed-` and preserving user-defined configurations. Secrets are interpolated from `.env` at deploy time so no credentials are committed to version control.

## MCP Servers

<!-- mcp:start -->

| Server | Description | Command | Required Secrets |
| --- | --- | --- | --- |

<!-- mcp:end -->

## Usage

### 1. Setup Environment
Copy `.env.example` to `.env` and fill in any required API keys or tokens:
```bash
cp .env.example .env
```

### 2. Deploy to Agents
Deploy all managed servers to supported agent configurations:
```bash
./run.sh deploy
# or
bun run deploy
```

### 3. Check Consistency
Verify manifest validity, secret coverage, and auto-generated documentation:
```bash
bun run check
```

### 4. Run Tests
Execute the test suite:
```bash
bun test
```
