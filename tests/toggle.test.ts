import { describe, expect, test, beforeEach, afterEach } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { setServerEnabled, runDeploy } from "../index";

describe("setServerEnabled and deploy filtering", () => {
  let tempDir: string;
  let tempHome: string;

  beforeEach(async () => {
    tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-toggle-test-"));
    tempHome = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-home-toggle-"));

    const initialManifest = {
      servers: {
        serverA: {
          command: "bunx",
          args: ["server-a"],
          description: "Server A description"
        },
        serverB: {
          command: "bunx",
          args: ["server-b"],
          description: "Server B description"
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

  test("setServerEnabled sets enabled: false when disabling existing server", async () => {
    const res = await setServerEnabled(tempDir, "serverA", false);
    expect(res.success).toBe(true);

    const updated = JSON.parse(await fs.promises.readFile(path.join(tempDir, "mcp-servers.json"), "utf-8"));
    expect(updated.servers.serverA.enabled).toBe(false);
  });

  test("setServerEnabled returns error when server does not exist", async () => {
    const res = await setServerEnabled(tempDir, "nonexistent", false);
    expect(res.success).toBe(false);
    expect(res.error).toContain("not found");
  });

  test("runDeploy excludes disabled servers from deployed configs", async () => {
    await setServerEnabled(tempDir, "serverA", false);
    const ok = await runDeploy(tempDir, tempHome);
    expect(ok).toBe(true);

    const claudePath = path.join(tempHome, ".claude.json");
    const claude = JSON.parse(await fs.promises.readFile(claudePath, "utf-8"));
    expect(claude.mcpServers["managed-serverA"]).toBeUndefined();
    expect(claude.mcpServers["managed-serverB"]).toBeDefined();
  });

  test("enabling a disabled server re-adds it to deployed configs", async () => {
    await setServerEnabled(tempDir, "serverA", false);
    await runDeploy(tempDir, tempHome);

    await setServerEnabled(tempDir, "serverA", true);
    await runDeploy(tempDir, tempHome);

    const claudePath = path.join(tempHome, ".claude.json");
    const claude = JSON.parse(await fs.promises.readFile(claudePath, "utf-8"));
    expect(claude.mcpServers["managed-serverA"]).toBeDefined();
  });
});
