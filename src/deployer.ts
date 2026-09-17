import fs from "node:fs";
import path from "node:path";

export interface AgentTarget {
  name: string;
  configPath: string;
  rootKey: "mcpServers" | "mcp";
  prefix: string;
}

export function mergeMcpConfig(
  existingConfig: any,
  managedServers: Record<string, any>,
  rootKey: "mcpServers" | "mcp" = "mcpServers",
  prefix: string = "managed-"
): any {
  const result = { ...(existingConfig || {}) };
  const currentServers = { ...(result[rootKey] || {}) };

  // Remove previously managed servers
  for (const key of Object.keys(currentServers)) {
    if (key.startsWith(prefix)) {
      delete currentServers[key];
    }
  }

  // Add updated managed servers
  for (const [name, def] of Object.entries(managedServers)) {
    const { description, ...cleanDef } = def;
    currentServers[`${prefix}${name}`] = cleanDef;
  }

  result[rootKey] = currentServers;
  return result;
}

export function getAgentTargets(homeDir: string): AgentTarget[] {
  return [
    {
      name: "Antigravity CLI",
      configPath: path.join(homeDir, ".gemini/antigravity-cli/mcp_config.json"),
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "Claude Desktop",
      configPath: path.join(homeDir, ".config/Claude/claude_desktop_config.json"),
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "Claude Code",
      configPath: path.join(homeDir, ".claude.json"),
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "Cursor",
      configPath: path.join(homeDir, ".cursor/mcp.json"),
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "OpenCode",
      configPath: path.join(homeDir, ".config/opencode/opencode.json"),
      rootKey: "mcp",
      prefix: "managed-"
    }
  ];
}

export async function deployToAgents(
  managedServers: Record<string, any>,
  homeDir: string = process.env.HOME || ""
): Promise<string[]> {
  const targets = getAgentTargets(homeDir);
  const reports: string[] = [];

  for (const target of targets) {
    try {
      const dir = path.dirname(target.configPath);
      await fs.promises.mkdir(dir, { recursive: true });

      let existing = {};
      if (fs.existsSync(target.configPath)) {
        try {
          const raw = await fs.promises.readFile(target.configPath, "utf-8");
          existing = JSON.parse(raw);
        } catch {
          existing = {};
        }
      }

      const merged = mergeMcpConfig(existing, managedServers, target.rootKey, target.prefix);
      await fs.promises.writeFile(target.configPath, JSON.stringify(merged, null, 2) + "\n");
      reports.push(`✓ ${target.name}: deployed ${Object.keys(managedServers).length} servers to ${target.configPath}`);
    } catch (err) {
      reports.push(`✗ ${target.name}: ${(err as Error).message}`);
    }
  }

  return reports;
}
