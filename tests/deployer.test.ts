import { expect, test, describe, beforeEach, afterEach } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { mergeMcpConfig, deployToAgents, formatServerForAgent } from "../src/deployer";

describe("formatServerForAgent", () => {
  test("formats properly for standard agents (stdio command)", () => {
    const server = {
      command: "bunx",
      args: ["my-server"],
      env: { KEY: "value" },
      description: "Sample description"
    };

    const formatted = formatServerForAgent(server, "standard");
    expect(formatted.command).toBe("bunx");
    expect(formatted.args).toEqual(["my-server"]);
    expect(formatted.env).toEqual({ KEY: "value" });
    expect(formatted.description).toBeUndefined();
  });

  test("formats properly for standard agents (remote url)", () => {
    const server = {
      url: "https://example.com/mcp",
      headers: { Authorization: "Bearer token" },
      description: "Remote server"
    };

    const formatted = formatServerForAgent(server, "standard");
    expect(formatted.url).toBe("https://example.com/mcp");
    expect(formatted.headers).toEqual({ Authorization: "Bearer token" });
    expect(formatted.command).toBeUndefined();
  });

  test("formats properly for OpenCode schema (type: local, command array, environment object)", () => {
    const server = {
      command: "bunx",
      args: ["my-server"],
      env: { KEY: "value" },
      description: "Sample description"
    };

    const formatted = formatServerForAgent(server, "opencode");
    expect(formatted.type).toBe("local");
    expect(formatted.command).toEqual(["bunx", "my-server"]);
    expect(formatted.environment).toEqual({ KEY: "value" });
    expect(formatted.env).toBeUndefined();
    expect(formatted.description).toBeUndefined();
  });

  test("formats properly for OpenCode schema (type: remote, url string, headers)", () => {
    const server = {
      url: "https://example.com/mcp",
      headers: { "X-API-Key": "key" }
    };

    const formatted = formatServerForAgent(server, "opencode");
    expect(formatted.type).toBe("remote");
    expect(formatted.url).toBe("https://example.com/mcp");
    expect(formatted.headers).toEqual({ "X-API-Key": "key" });
  });

  test("formats properly for Codex TOML with toolTimeoutSec", () => {
    const server = {
      command: "bunx",
      args: ["slow-server"],
      toolTimeoutSec: 960
    };

    const formatted = formatServerForAgent(server, "toml");
    expect(formatted.command).toBe("bunx");
    expect(formatted.tool_timeout_sec).toBe(960);
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
        command: "bunx",
        args: ["server-filesystem"],
        description: "Local filesystem operations"
      }
    };

    const updated = mergeMcpConfig(existingConfig, managedServers, "mcpServers", "managed-", "standard");

    expect(updated.mcpServers["user-custom-server"]).toBeDefined();
    expect(updated.mcpServers["managed-old"]).toBeUndefined();
    expect(updated.mcpServers["managed-filesystem"]).toBeDefined();
    expect(updated.mcpServers["managed-filesystem"].command).toBe("bunx");
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

  test("deploys managed servers across all 6 agent configs including Codex TOML", async () => {
    // Pre-populate Codex TOML config with existing user settings
    const codexPath = path.join(tempDir, ".codex/config.toml");
    await fs.promises.mkdir(path.dirname(codexPath), { recursive: true });
    await fs.promises.writeFile(
      codexPath,
      'model = "gpt-5.6-luna"\n[mcp_servers.user_server]\ncommand = "my-cmd"\n'
    );

    const managedServers = {
      filesystem: {
        command: "bunx",
        args: ["@modelcontextprotocol/server-filesystem"],
        description: "Filesystem server"
      },
      github: {
        command: "bunx",
        args: ["@modelcontextprotocol/server-github"],
        env: { GITHUB_PERSONAL_ACCESS_TOKEN: "secret-token" }
      },
      docs: {
        url: "https://example.com/mcp",
        description: "Remote docs"
      }
    };

    const reports = await deployToAgents(managedServers, tempDir);
    expect(reports.length).toBe(6);

    // 1. Verify Standard agent (Claude Code / Antigravity / Cursor)
    const claudeCodePath = path.join(tempDir, ".claude.json");
    const parsedClaude = JSON.parse(await fs.promises.readFile(claudeCodePath, "utf-8"));
    expect(parsedClaude.mcpServers["managed-filesystem"].command).toBe("bunx");
    expect(parsedClaude.mcpServers["managed-github"].env.GITHUB_PERSONAL_ACCESS_TOKEN).toBe("secret-token");
    expect(parsedClaude.mcpServers["managed-docs"].url).toBe("https://example.com/mcp");

    // 2. Verify OpenCode agent conforms to OpenCode schema
    const openCodePath = path.join(tempDir, ".config/opencode/opencode.json");
    const parsedOpenCode = JSON.parse(await fs.promises.readFile(openCodePath, "utf-8"));
    expect(parsedOpenCode.mcp["managed-filesystem"].type).toBe("local");
    expect(parsedOpenCode.mcp["managed-filesystem"].command).toEqual(["bunx", "@modelcontextprotocol/server-filesystem"]);
    expect(parsedOpenCode.mcp["managed-docs"].type).toBe("remote");
    expect(parsedOpenCode.mcp["managed-docs"].url).toBe("https://example.com/mcp");

    // 3. Verify Codex CLI TOML config
    const codexContent = await fs.promises.readFile(codexPath, "utf-8");
    const parsedCodex: any = Bun.TOML.parse(codexContent);
    expect(parsedCodex.model).toBe("gpt-5.6-luna");
    expect(parsedCodex.mcp_servers.user_server).toBeDefined();
    expect(parsedCodex.mcp_servers["managed-filesystem"]).toBeDefined();
    expect(parsedCodex.mcp_servers["managed-filesystem"].command).toBe("bunx");
    expect(parsedCodex.mcp_servers["managed-github"].env.GITHUB_PERSONAL_ACCESS_TOKEN).toBe("secret-token");
    expect(parsedCodex.mcp_servers["managed-docs"].url).toBe("https://example.com/mcp");
  });
});
