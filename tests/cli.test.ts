import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {
  extractReferencedEnvVars,
  runCheck,
  runDeploy,
  runDisable,
  runEnable,
  runList,
  runSync
} from "../index";

describe("extractReferencedEnvVars", () => {
  test("extracts variables from template strings and server.env keys, ignoring HOME", () => {
    const servers = {
      github: {
        command: "npx",
        env: {
          GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
        }
      },
      custom: {
        command: "run",
        args: ["--path", "${HOME}/files", "--key", "${CUSTOM_SECRET}"],
        env: {
          STATIC_KEY: "static"
        }
      }
    };

    const vars = extractReferencedEnvVars(servers);
    expect(vars).toEqual(["CUSTOM_SECRET", "GITHUB_PERSONAL_ACCESS_TOKEN"]);
  });
});

describe("runCheck", () => {
  let tempDir: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-cli-test-"));
  });

  afterEach(async () => {
    await fs.promises.rm(tempDir, { recursive: true, force: true });
  });

  test("returns false when referenced env var is missing from .env.example", async () => {
    const servers = {
      github: {
        command: "npx",
        env: {
          GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
        }
      }
    };

    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );
    await fs.promises.writeFile(
      path.join(tempDir, ".env.example"),
      "# Missing GITHUB_PERSONAL_ACCESS_TOKEN\nOTHER_KEY=\n"
    );

    const passed = await runCheck(tempDir);
    expect(passed).toBe(false);
  });

  test("returns true when .env.example documents all referenced secrets", async () => {
    const servers = {
      github: {
        command: "npx",
        env: {
          GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
        }
      }
    };

    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );
    await fs.promises.writeFile(
      path.join(tempDir, ".env.example"),
      "GITHUB_PERSONAL_ACCESS_TOKEN=\n"
    );

    const passed = await runCheck(tempDir);
    expect(passed).toBe(true);
  });
});

describe("runDeploy and runSync", () => {
  let tempDir: string;
  let tempHome: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-deploy-cli-"));
    tempHome = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-home-cli-"));
  });

  afterEach(async () => {
    await fs.promises.rm(tempDir, { recursive: true, force: true });
    await fs.promises.rm(tempHome, { recursive: true, force: true });
  });

  test("runDeploy interpolates secrets, resolves ${HOME}, and writes agent target configs", async () => {
    const servers = {
      filesystem: {
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem", "${HOME}/docs"],
        description: "Filesystem server"
      },
      github: {
        command: "npx",
        env: { GITHUB_TOKEN: "${GITHUB_TOKEN}" },
        description: "GitHub server"
      }
    };

    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );
    await fs.promises.writeFile(
      path.join(tempDir, ".env"),
      "GITHUB_TOKEN=ghp_secret_12345\n"
    );

    const ok = await runDeploy(tempDir, tempHome);
    expect(ok).toBe(true);

    // Verify config deployed to fake home
    const claudePath = path.join(tempHome, ".claude.json");
    const rawClaude = await fs.promises.readFile(claudePath, "utf-8");
    const parsedClaude = JSON.parse(rawClaude);
    expect(parsedClaude.mcpServers["managed-filesystem"].args[2]).toBe(path.join(tempHome, "docs"));
    expect(parsedClaude.mcpServers["managed-github"].env.GITHUB_TOKEN).toBe("ghp_secret_12345");
  });

  test("runSync executes without throwing", async () => {
    const servers = {
      fetch: { command: "uvx", args: ["mcp-server-fetch"] }
    };
    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );

    await expect(runSync(tempDir)).resolves.toBeUndefined();
  });
});

describe("runEnable and runDisable", () => {
  let tempDir: string;
  let tempHome: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-cli-toggle-"));
    tempHome = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-home-toggle-"));

    const initialManifest = {
      servers: {
        serverA: {
          command: "bunx",
          args: ["server-a"],
          description: "Server A"
        },
        serverB: {
          command: "bunx",
          args: ["server-b"],
          description: "Server B"
        }
      }
    };
    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify(initialManifest, null, 2)
    );
  });

  afterEach(async () => {
    await fs.promises.rm(tempDir, { recursive: true, force: true });
    await fs.promises.rm(tempHome, { recursive: true, force: true });
  });

  test("runDisable disables server and removes it from agent configs", async () => {
    await runDeploy(tempDir, tempHome);
    const claudePath = path.join(tempHome, ".claude.json");
    let claude = JSON.parse(await fs.promises.readFile(claudePath, "utf-8"));
    expect(claude.mcpServers["managed-serverA"]).toBeDefined();
    expect(claude.mcpServers["managed-serverB"]).toBeDefined();

    const res = await runDisable("serverA", tempDir, tempHome);
    expect(res).toBe(true);

    const manifest = JSON.parse(
      await fs.promises.readFile(path.join(tempDir, "mcp-servers.json"), "utf-8")
    );
    expect(manifest.servers.serverA.enabled).toBe(false);

    claude = JSON.parse(await fs.promises.readFile(claudePath, "utf-8"));
    expect(claude.mcpServers["managed-serverA"]).toBeUndefined();
    expect(claude.mcpServers["managed-serverB"]).toBeDefined();
  });

  test("runEnable enables server and updates agent configs", async () => {
    await runDisable("serverA", tempDir, tempHome);
    const claudePath = path.join(tempHome, ".claude.json");
    let claude = JSON.parse(await fs.promises.readFile(claudePath, "utf-8"));
    expect(claude.mcpServers["managed-serverA"]).toBeUndefined();

    const res = await runEnable("serverA", tempDir, tempHome);
    expect(res).toBe(true);

    const manifest = JSON.parse(
      await fs.promises.readFile(path.join(tempDir, "mcp-servers.json"), "utf-8")
    );
    expect(manifest.servers.serverA.enabled).toBeUndefined();

    claude = JSON.parse(await fs.promises.readFile(claudePath, "utf-8"));
    expect(claude.mcpServers["managed-serverA"]).toBeDefined();
  });

  test("runEnable and runDisable return false on missing arguments or non-existent servers", async () => {
    const resNoNameEnable = await runEnable(undefined, tempDir, tempHome);
    expect(resNoNameEnable).toBe(false);

    const resNoNameDisable = await runDisable(undefined, tempDir, tempHome);
    expect(resNoNameDisable).toBe(false);

    const resNotFoundEnable = await runEnable("nonexistent", tempDir, tempHome);
    expect(resNotFoundEnable).toBe(false);

    const resNotFoundDisable = await runDisable("nonexistent", tempDir, tempHome);
    expect(resNotFoundDisable).toBe(false);
  });

  test("runList executes cleanly without throwing", async () => {
    const servers = {
      serverA: { command: "bunx", args: ["server-a"], description: "Server A" },
      serverB: { command: "bunx", args: ["server-b"], enabled: false, description: "Server B" }
    };
    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );

    await expect(runList(tempDir)).resolves.toBeUndefined();
  });

  test("corrupt manifest does not throw uncaught error and returns false/error", async () => {
    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      "{ invalid json syntax ... "
    );

    const deployOk = await runDeploy(tempDir, tempHome);
    expect(deployOk).toBe(false);

    const checkOk = await runCheck(tempDir);
    expect(checkOk).toBe(false);
  });
});

