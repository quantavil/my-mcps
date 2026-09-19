import fs from "node:fs";
import path from "node:path";

export interface AgentTarget {
  name: string;
  configPath: string;
  format: "standard" | "opencode" | "toml";
  rootKey: string;
  prefix: string;
}

export function formatServerForAgent(
  serverDef: Record<string, any>,
  format: "standard" | "opencode" | "toml"
): Record<string, any> {
  const { description, ...cleanDef } = serverDef;

  if (format === "opencode") {
    // OpenCode schema: type="local", command=[cmd, ...args], environment={...}
    const commandList = [
      cleanDef.command,
      ...(Array.isArray(cleanDef.args) ? cleanDef.args : [])
    ].filter(Boolean);

    const result: Record<string, any> = {
      type: "local",
      command: commandList
    };

    if (cleanDef.env && Object.keys(cleanDef.env).length > 0) {
      result.environment = cleanDef.env;
    }

    return result;
  }

  // Standard format (Antigravity, Claude, Cursor, Codex TOML)
  const result: Record<string, any> = {
    command: cleanDef.command,
    args: Array.isArray(cleanDef.args) ? cleanDef.args : []
  };

  if (cleanDef.env && Object.keys(cleanDef.env).length > 0) {
    result.env = cleanDef.env;
  }

  return result;
}

export function mergeMcpConfig(
  existingConfig: any,
  managedServers: Record<string, any>,
  rootKey: string = "mcpServers",
  prefix: string = "managed-",
  format: "standard" | "opencode" | "toml" = "standard"
): any {
  const result = { ...(existingConfig || {}) };
  const currentServers = { ...(result[rootKey] || {}) };

  // Remove previously managed servers
  for (const key of Object.keys(currentServers)) {
    if (key.startsWith(prefix)) {
      delete currentServers[key];
    }
  }

  // Add updated managed servers with target-specific format
  for (const [name, def] of Object.entries(managedServers)) {
    currentServers[`${prefix}${name}`] = formatServerForAgent(def, format);
  }

  result[rootKey] = currentServers;
  return result;
}

export function getAgentTargets(homeDir: string): AgentTarget[] {
  return [
    {
      name: "Antigravity CLI",
      configPath: path.join(homeDir, ".gemini/config/mcp_config.json"),
      format: "standard",
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "Claude Desktop",
      configPath: path.join(homeDir, ".config/Claude/claude_desktop_config.json"),
      format: "standard",
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "Claude Code",
      configPath: path.join(homeDir, ".claude.json"),
      format: "standard",
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "Cursor",
      configPath: path.join(homeDir, ".cursor/mcp.json"),
      format: "standard",
      rootKey: "mcpServers",
      prefix: "managed-"
    },
    {
      name: "OpenCode",
      configPath: path.join(homeDir, ".config/opencode/opencode.json"),
      format: "opencode",
      rootKey: "mcp",
      prefix: "managed-"
    },
    {
      name: "Codex CLI",
      configPath: path.join(homeDir, ".codex/config.toml"),
      format: "toml",
      rootKey: "mcp_servers",
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

      let existing: any = {};
      const isToml = target.format === "toml" || target.configPath.endsWith(".toml");

      if (fs.existsSync(target.configPath)) {
        try {
          const raw = await fs.promises.readFile(target.configPath, "utf-8");
          if (isToml) {
            existing = Bun.TOML.parse(raw);
          } else {
            existing = JSON.parse(raw);
          }
        } catch {
          const bakPath = `${target.configPath}.bak`;
          try {
            await fs.promises.copyFile(target.configPath, bakPath);
            reports.push(`⚠️  ${target.name}: unparseable config backed up to ${bakPath}`);
          } catch {}
          existing = {};
        }
      }

      const merged = mergeMcpConfig(
        existing,
        managedServers,
        target.rootKey,
        target.prefix,
        target.format
      );

      if (isToml) {
        const serialized = Bun.TOML.stringify(merged) ?? "";
        await fs.promises.writeFile(target.configPath, serialized);
      } else {
        await fs.promises.writeFile(target.configPath, JSON.stringify(merged, null, 2) + "\n");
      }

      reports.push(`✓ ${target.name}: deployed ${Object.keys(managedServers).length} servers to ${target.configPath}`);
    } catch (err) {
      reports.push(`✗ ${target.name}: ${(err as Error).message}`);
    }
  }

  return reports;
}
