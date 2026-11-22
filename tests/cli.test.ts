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
  setServerEnabled
} from "../index";
import { validateServers } from "../src/manifest";

describe("validateServers", () => {
  test("flags servers that specify neither command nor url", () => {
    const problems = validateServers({
      okCmd: { command: "x" },
      okUrl: { url: "https://example.com" },
      invalid: { description: "Missing both" }
    });
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("'invalid' must specify either 'command' or 'url'");
  });
});

describe("extractReferencedEnvVars", () => {
  test("extracts variables from template strings and server.env keys, ignoring HOME and MCP_ROOT", () => {
    const servers = {
      github: {
        command: "npx",
        env: {
          GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
        }
      },
      custom: {
        command: "run",
        args: ["--path", "${HOME}/files", "--root", "${MCP_ROOT}", "--key", "${CUSTOM_SECRET}"],
        env: {
          STATIC_KEY: "static"
        }
      }
    };

    const vars = extractReferencedEnvVars(servers);
    expect(vars).toEqual(["CUSTOM_SECRET", "GITHUB_PERSONAL_ACCESS_TOKEN"]);
  });
});

describe("setServerEnabled", () => {
  let tempDir: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-toggle-test-"));
    const manifest = {
      servers: {
        serverA: { command: "bunx", args: ["server-a"] }
      }
    };
    await fs.promises.writeFile(path.join(tempDir, "mcp-servers.json"), JSON.stringify(manifest, null, 2));
  });

  afterEach(async () => {
    await fs.promises.rm(tempDir, { recursive: true, force: true });
  });

  test("sets enabled: false when disabling and removes enabled when re-enabling", async () => {
    let res = await setServerEnabled(tempDir, "serverA", false);
    expect(res.success).toBe(true);

    let manifest = JSON.parse(await fs.promises.readFile(path.join(tempDir, "mcp-servers.json"), "utf-8"));
    expect(manifest.servers.serverA.enabled).toBe(false);

    res = await setServerEnabled(tempDir, "serverA", true);
    expect(res.success).toBe(true);

    manifest = JSON.parse(await fs.promises.readFile(path.join(tempDir, "mcp-servers.json"), "utf-8"));
    expect(manifest.servers.serverA.enabled).toBeUndefined();
  });

  test("returns error when server does not exist", async () => {
    const res = await setServerEnabled(tempDir, "nonexistent", false);
    expect(res.success).toBe(false);
    expect(res.error).toContain("not found");
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

  test("returns false when a server definition is missing command and url", async () => {
    const servers = {
      broken: {
        description: "No command or url"
      }
    };

    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );
    await fs.promises.writeFile(
      path.join(tempDir, ".env.example"),
      ""
    );

    const passed = await runCheck(tempDir);
    expect(passed).toBe(false);
  });
});

describe("runDeploy", () => {
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
      },
      remote: {
        url: "https://example.com/mcp",
        headers: { "X-Token": "${REMOTE_TOKEN}" }
      }
    };

    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );
    await fs.promises.writeFile(
      path.join(tempDir, ".env"),
      "GITHUB_TOKEN=ghp_secret_12345\nREMOTE_TOKEN=remote_secret\n"
    );

    const ok = await runDeploy(tempDir, tempHome);
    expect(ok).toBe(true);

    // Verify config deployed to fake home
    const claudePath = path.join(tempHome, ".claude.json");
    const rawClaude = await fs.promises.readFile(claudePath, "utf-8");
    const parsedClaude = JSON.parse(rawClaude);
    expect(path.normalize(parsedClaude.mcpServers["managed-filesystem"].args[2])).toBe(path.normalize(path.join(tempHome, "docs")));
    expect(parsedClaude.mcpServers["managed-github"].env.GITHUB_TOKEN).toBe("ghp_secret_12345");
    expect(parsedClaude.mcpServers["managed-remote"].url).toBe("https://example.com/mcp");
    expect(parsedClaude.mcpServers["managed-remote"].headers["X-Token"]).toBe("remote_secret");
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
