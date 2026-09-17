import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { extractReferencedEnvVars, runCheck, runDeploy, runSync } from "../index";
import { START_TAG, END_TAG, renderReadmeTable, updateReadmeContent } from "../src/catalog";

describe("extractReferencedEnvVars", () => {
  test("extracts variables from template strings and server.env keys", () => {
    const servers = {
      github: {
        command: "npx",
        env: {
          GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
        }
      },
      custom: {
        command: "run",
        args: ["--key", "${CUSTOM_SECRET}"],
        env: {
          STATIC_KEY: "static"
        }
      }
    };

    const vars = extractReferencedEnvVars(servers);
    expect(vars).toEqual(["CUSTOM_SECRET", "GITHUB_PERSONAL_ACCESS_TOKEN", "STATIC_KEY"]);
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

  test("returns false when README.md catalog table is outdated", async () => {
    const servers = {
      filesystem: {
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem"],
        description: "Local filesystem operations"
      }
    };

    await fs.promises.writeFile(
      path.join(tempDir, "mcp-servers.json"),
      JSON.stringify({ servers })
    );
    await fs.promises.writeFile(
      path.join(tempDir, ".env.example"),
      "# No secrets required\n"
    );
    await fs.promises.writeFile(
      path.join(tempDir, "README.md"),
      `# Header\n\n${START_TAG}\n\n| Stale | Table |\n\n${END_TAG}\n`
    );

    const passed = await runCheck(tempDir);
    expect(passed).toBe(false);
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
    const table = renderReadmeTable(servers);
    const readme = updateReadmeContent(
      `# Header\n\n${START_TAG}\n${END_TAG}\n`,
      table
    );
    await fs.promises.writeFile(path.join(tempDir, "README.md"), readme);

    const passed = await runCheck(tempDir);
    expect(passed).toBe(false);
  });

  test("returns true when README and .env.example are fully valid", async () => {
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
    const table = renderReadmeTable(servers);
    const readme = updateReadmeContent(
      `# Header\n\n${START_TAG}\n${END_TAG}\n`,
      table
    );
    await fs.promises.writeFile(path.join(tempDir, "README.md"), readme);

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

  test("runDeploy updates README and writes agent target configs", async () => {
    const servers = {
      filesystem: {
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem"],
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
    await fs.promises.writeFile(
      path.join(tempDir, "README.md"),
      `# Header\n\n${START_TAG}\n\n${END_TAG}\n`
    );

    const ok = await runDeploy(tempDir, tempHome);
    expect(ok).toBe(true);

    // Verify README updated
    const updatedReadme = await fs.promises.readFile(path.join(tempDir, "README.md"), "utf-8");
    expect(updatedReadme).toContain("| `filesystem` | Filesystem server | `npx -y @modelcontextprotocol/server-filesystem` | None |");
    expect(updatedReadme).toContain("| `github` | GitHub server | `npx` | `GITHUB_TOKEN` |");

    // Verify config deployed to fake home
    const claudePath = path.join(tempHome, ".claude.json");
    const rawClaude = await fs.promises.readFile(claudePath, "utf-8");
    const parsedClaude = JSON.parse(rawClaude);
    expect(parsedClaude.mcpServers["managed-filesystem"]).toBeDefined();
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
