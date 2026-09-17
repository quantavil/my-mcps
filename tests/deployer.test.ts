import { expect, test, describe, beforeEach, afterEach } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { mergeMcpConfig, getAgentTargets, deployToAgents } from "../src/deployer";

describe("mergeMcpConfig", () => {
  test("merges managed servers without overwriting user servers", () => {
    const existingConfig = {
      mcpServers: {
        "user-custom-server": { command: "custom-cmd", args: [] },
        "managed-old": { command: "old-cmd", args: [] }
      }
    };

    const managedServers = {
      filesystem: {
        command: "npx",
        args: ["server-filesystem"],
        description: "Local filesystem operations"
      }
    };

    const updated = mergeMcpConfig(existingConfig, managedServers, "mcpServers", "managed-");

    // Preserves user servers
    expect(updated.mcpServers["user-custom-server"]).toBeDefined();
    expect(updated.mcpServers["user-custom-server"].command).toBe("custom-cmd");
    // Removes obsolete managed servers
    expect(updated.mcpServers["managed-old"]).toBeUndefined();
    // Adds new managed server with prefix
    expect(updated.mcpServers["managed-filesystem"]).toBeDefined();
    expect(updated.mcpServers["managed-filesystem"].command).toBe("npx");
    // Strips description
    expect(updated.mcpServers["managed-filesystem"].description).toBeUndefined();
  });

  test("handles empty or undefined existingConfig and custom rootKey", () => {
    const managedServers = {
      fetch: { command: "uvx", args: ["mcp-server-fetch"] }
    };

    const updatedOpenCode = mergeMcpConfig(undefined, managedServers, "mcp", "managed-");
    expect(updatedOpenCode.mcp).toBeDefined();
    expect(updatedOpenCode.mcp["managed-fetch"]).toBeDefined();
    expect(updatedOpenCode.mcp["managed-fetch"].command).toBe("uvx");
  });

  test("preserves other top-level keys in existingConfig", () => {
    const existingConfig = {
      theme: "dark",
      settings: { fontSize: 14 },
      mcpServers: {
        "user-server": { command: "user" }
      }
    };

    const managedServers = {
      memory: { command: "npx", args: ["server-memory"] }
    };

    const updated = mergeMcpConfig(existingConfig, managedServers, "mcpServers", "managed-");
    expect(updated.theme).toBe("dark");
    expect(updated.settings.fontSize).toBe(14);
    expect(updated.mcpServers["user-server"]).toBeDefined();
    expect(updated.mcpServers["managed-memory"]).toBeDefined();
  });
});

describe("getAgentTargets", () => {
  test("returns all expected agent targets with appropriate configurations", () => {
    const fakeHome = "/tmp/fake-home";
    const targets = getAgentTargets(fakeHome);

    expect(targets.length).toBe(5);

    const targetMap = new Map(targets.map(t => [t.name, t]));

    const antigravity = targetMap.get("Antigravity CLI");
    expect(antigravity).toBeDefined();
    expect(antigravity?.configPath).toBe(path.join(fakeHome, ".gemini/antigravity-cli/mcp_config.json"));
    expect(antigravity?.rootKey).toBe("mcpServers");
    expect(antigravity?.prefix).toBe("managed-");

    const claudeDesktop = targetMap.get("Claude Desktop");
    expect(claudeDesktop).toBeDefined();
    expect(claudeDesktop?.configPath).toBe(path.join(fakeHome, ".config/Claude/claude_desktop_config.json"));
    expect(claudeDesktop?.rootKey).toBe("mcpServers");

    const claudeCode = targetMap.get("Claude Code");
    expect(claudeCode).toBeDefined();
    expect(claudeCode?.configPath).toBe(path.join(fakeHome, ".claude.json"));
    expect(claudeCode?.rootKey).toBe("mcpServers");

    const cursor = targetMap.get("Cursor");
    expect(cursor).toBeDefined();
    expect(cursor?.configPath).toBe(path.join(fakeHome, ".cursor/mcp.json"));
    expect(cursor?.rootKey).toBe("mcpServers");

    const openCode = targetMap.get("OpenCode");
    expect(openCode).toBeDefined();
    expect(openCode?.configPath).toBe(path.join(fakeHome, ".config/opencode/opencode.json"));
    expect(openCode?.rootKey).toBe("mcp");
    expect(openCode?.prefix).toBe("managed-");
  });
});

describe("deployToAgents", () => {
  let tempDir: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-deploy-test-"));
  });

  afterEach(async () => {
    await fs.promises.rm(tempDir, { recursive: true, force: true });
  });

  test("deploys managed servers across agent configs non-destructively", async () => {
    // Pre-populate one of the config files with user custom config
    const claudeCodePath = path.join(tempDir, ".claude.json");
    await fs.promises.writeFile(
      claudeCodePath,
      JSON.stringify({
        mcpServers: {
          "user-personal-mcp": { command: "personal-cmd", args: [] },
          "managed-deprecated": { command: "old" }
        },
        userSetting: true
      })
    );

    const managedServers = {
      filesystem: {
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem"],
        description: "Filesystem server"
      },
      github: {
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-github"],
        env: { GITHUB_PERSONAL_ACCESS_TOKEN: "secret-token" }
      }
    };

    const reports = await deployToAgents(managedServers, tempDir);

    expect(reports.length).toBe(5);
    for (const report of reports) {
      expect(report.startsWith("✓")).toBe(true);
    }

    // Verify Claude Code config was merged non-destructively
    const rawClaude = await fs.promises.readFile(claudeCodePath, "utf-8");
    const parsedClaude = JSON.parse(rawClaude);
    expect(parsedClaude.userSetting).toBe(true);
    expect(parsedClaude.mcpServers["user-personal-mcp"]).toBeDefined();
    expect(parsedClaude.mcpServers["managed-deprecated"]).toBeUndefined();
    expect(parsedClaude.mcpServers["managed-filesystem"]).toBeDefined();
    expect(parsedClaude.mcpServers["managed-filesystem"].description).toBeUndefined();
    expect(parsedClaude.mcpServers["managed-github"]).toBeDefined();
    expect(parsedClaude.mcpServers["managed-github"].env.GITHUB_PERSONAL_ACCESS_TOKEN).toBe("secret-token");

    // Verify OpenCode uses rootKey "mcp"
    const openCodePath = path.join(tempDir, ".config/opencode/opencode.json");
    const rawOpenCode = await fs.promises.readFile(openCodePath, "utf-8");
    const parsedOpenCode = JSON.parse(rawOpenCode);
    expect(parsedOpenCode.mcp).toBeDefined();
    expect(parsedOpenCode.mcp["managed-filesystem"]).toBeDefined();
    expect(parsedOpenCode.mcp["managed-github"]).toBeDefined();

    // Verify Antigravity CLI
    const antigravityPath = path.join(tempDir, ".gemini/antigravity-cli/mcp_config.json");
    const rawAntigravity = await fs.promises.readFile(antigravityPath, "utf-8");
    const parsedAntigravity = JSON.parse(rawAntigravity);
    expect(parsedAntigravity.mcpServers["managed-filesystem"]).toBeDefined();
  });
});
