import { expect, test, describe, beforeEach, afterEach } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { mergeMcpConfig, getAgentTargets, deployToAgents, formatServerForAgent } from "../src/deployer";

describe("formatServerForAgent", () => {
  test("formats properly for standard agents", () => {
    const server = {
      command: "npx",
      args: ["-y", "my-server"],
      env: { KEY: "value" },
      description: "Sample description"
    };

    const formatted = formatServerForAgent(server, "standard");
    expect(formatted.command).toBe("npx");
    expect(formatted.args).toEqual(["-y", "my-server"]);
    expect(formatted.env).toEqual({ KEY: "value" });
    expect(formatted.description).toBeUndefined();
  });

  test("formats properly for OpenCode schema (type: local, command array, environment object)", () => {
    const server = {
      command: "npx",
      args: ["-y", "my-server"],
      env: { KEY: "value" },
      description: "Sample description"
    };

    const formatted = formatServerForAgent(server, "opencode");
    expect(formatted.type).toBe("local");
    expect(formatted.command).toEqual(["npx", "-y", "my-server"]);
    expect(formatted.environment).toEqual({ KEY: "value" });
    expect(formatted.env).toBeUndefined();
    expect(formatted.description).toBeUndefined();
  });
});

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

    const updated = mergeMcpConfig(existingConfig, managedServers, "mcpServers", "managed-", "standard");

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
});

describe("deployToAgents", () => {
  let tempDir: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-deploy-test-"));
  });

  afterEach(async () => {
    await fs.promises.rm(tempDir, { recursive: true, force: true });
  });

  test("deploys managed servers across agent configs with format compliance", async () => {
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

    // 1. Verify Standard agent (Claude Code / Antigravity / Cursor)
    const claudeCodePath = path.join(tempDir, ".claude.json");
    const parsedClaude = JSON.parse(await fs.promises.readFile(claudeCodePath, "utf-8"));
    expect(parsedClaude.mcpServers["managed-filesystem"].command).toBe("npx");
    expect(parsedClaude.mcpServers["managed-filesystem"].args).toEqual(["-y", "@modelcontextprotocol/server-filesystem"]);
    expect(parsedClaude.mcpServers["managed-github"].env.GITHUB_PERSONAL_ACCESS_TOKEN).toBe("secret-token");

    // 2. Verify OpenCode agent conforms to OpenCode schema
    const openCodePath = path.join(tempDir, ".config/opencode/opencode.json");
    const parsedOpenCode = JSON.parse(await fs.promises.readFile(openCodePath, "utf-8"));
    expect(parsedOpenCode.mcp).toBeDefined();
    expect(parsedOpenCode.mcp["managed-filesystem"].type).toBe("local");
    expect(parsedOpenCode.mcp["managed-filesystem"].command).toEqual(["npx", "-y", "@modelcontextprotocol/server-filesystem"]);
    expect(parsedOpenCode.mcp["managed-github"].environment.GITHUB_PERSONAL_ACCESS_TOKEN).toBe("secret-token");
  });
});
