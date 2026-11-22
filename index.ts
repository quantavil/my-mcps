import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { deployToAgents } from "./src/deployer";
import { buildEnv, isEnabled, loadManifest, resolveServers, validateServers } from "./src/manifest";
import { parseEnv } from "./src/secrets";

export function extractReferencedEnvVars(servers: Record<string, any>): string[] {
  const vars = new Set<string>();
  const jsonStr = JSON.stringify(servers);
  const matches = jsonStr.matchAll(/\${([A-Z0-9_]+)}/g);
  for (const match of matches) {
    const varName = match[1];
    if (varName && !["HOME", "MCP_ROOT"].includes(varName)) {
      vars.add(varName);
    }
  }
  return Array.from(vars).sort();
}

export async function setServerEnabled(
  rootDir: string,
  serverName: string,
  enabled: boolean
): Promise<{ success: boolean; error?: string }> {
  const { manifest, error } = loadManifest(rootDir);
  if (!manifest) {
    return { success: false, error };
  }
  if (!manifest.servers || !manifest.servers[serverName]) {
    return { success: false, error: `Server '${serverName}' not found in manifest` };
  }

  if (enabled) {
    delete manifest.servers[serverName].enabled; // default is enabled
  } else {
    manifest.servers[serverName].enabled = false;
  }

  const manifestPath = path.join(rootDir, "mcp-servers.json");
  await fs.promises.writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
  return { success: true };
}

export async function runEnable(
  serverName?: string,
  rootDir: string = import.meta.dir,
  homeDir: string = os.homedir()
): Promise<boolean> {
  if (!serverName) {
    console.error("✗ Usage: ./run.sh enable <server-name>");
    return false;
  }
  const res = await setServerEnabled(rootDir, serverName, true);
  if (!res.success) {
    console.error(`✗ ${res.error}`);
    return false;
  }
  console.log(`✓ Server '${serverName}' enabled`);
  return await runDeploy(rootDir, homeDir);
}

export async function runDisable(
  serverName?: string,
  rootDir: string = import.meta.dir,
  homeDir: string = os.homedir()
): Promise<boolean> {
  if (!serverName) {
    console.error("✗ Usage: ./run.sh disable <server-name>");
    return false;
  }
  const res = await setServerEnabled(rootDir, serverName, false);
  if (!res.success) {
    console.error(`✗ ${res.error}`);
    return false;
  }
  console.log(`✓ Server '${serverName}' disabled (inactive in repo, removed from agent targets)`);
  return await runDeploy(rootDir, homeDir);
}

export async function runDeploy(
  rootDir: string = import.meta.dir,
  homeDir: string = os.homedir()
): Promise<boolean> {
  const { manifest, error } = loadManifest(rootDir);
  if (!manifest) {
    console.error(`✗ ${error}`);
    return false;
  }

  const allServers = manifest.servers || {};
  const env = buildEnv(rootDir, homeDir);

  const { servers: deployed, missing } = resolveServers(allServers, env);
  if (missing.length > 0) {
    console.warn(`⚠️  Warning: Missing secrets (omitted from deployed servers): ${missing.join(", ")}`);
  }

  const reports = await deployToAgents(deployed, homeDir);
  for (const report of reports) {
    console.log(report);
  }

  return true;
}

export async function runList(rootDir: string = import.meta.dir): Promise<void> {
  const { manifest, error } = loadManifest(rootDir);
  if (!manifest) {
    console.error(`✗ ${error}`);
    return;
  }

  const servers = manifest.servers || {};
  const isTTY = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR;
  const green = (s: string) => (isTTY ? `\x1b[32m${s}\x1b[0m` : s);
  const red = (s: string) => (isTTY ? `\x1b[31m${s}\x1b[0m` : s);
  const bold = (s: string) => (isTTY ? `\x1b[1m${s}\x1b[0m` : s);

  console.log("\nConfigured MCP Servers:");
  console.log("─".repeat(70));
  for (const [name, def] of Object.entries(servers)) {
    const d = def as any;
    const on = isEnabled(d);
    const statusTag = on ? green("[enabled]") : red("[disabled]");
    const cmd = d.url ? d.url : `${d.command} ${(d.args || []).join(" ")}`;
    const envKeys = d.env ? Object.keys(d.env) : [];
    console.log(`• ${bold(name)} ${statusTag}: ${d.description || ""}`);
    console.log(`  ${d.url ? "URL" : "Command"}: ${cmd}`);
    if (envKeys.length > 0) {
      console.log(`  Secrets: ${envKeys.join(", ")}`);
    }
    console.log();
  }
}

export async function runCheck(rootDir: string = import.meta.dir): Promise<boolean> {
  let passed = true;

  const { manifest, error } = loadManifest(rootDir);
  if (!manifest) {
    console.error(`✗ ${error}`);
    return false;
  }

  const servers = manifest.servers || {};

  const serverProblems = validateServers(servers);
  if (serverProblems.length > 0) {
    for (const problem of serverProblems) console.error(`✗ ${problem}`);
    passed = false;
  } else {
    console.log("✓ Every server has a valid configuration");
  }

  // Validate .env.example coverage
  const envExamplePath = path.join(rootDir, ".env.example");
  if (!fs.existsSync(envExamplePath)) {
    console.error(`✗ Missing .env.example file at ${envExamplePath}`);
    passed = false;
  } else {
    const envExampleRaw = await fs.promises.readFile(envExamplePath, "utf-8");
    const exampleEnv = parseEnv(envExampleRaw);
    const referencedVars = extractReferencedEnvVars(servers);
    const missingVars = referencedVars.filter((v) => !(v in exampleEnv));

    if (missingVars.length > 0) {
      console.error(
        `✗ Missing environment variable(s) in .env.example: ${missingVars.join(", ")}`
      );
      passed = false;
    } else {
      console.log("✓ All referenced environment variables are documented in .env.example");
    }
  }

  return passed;
}

export function printHelp(): void {
  console.log(`my-mcps: Centralized MCP Server Manager

Usage:
  ./run.sh [command]
  bun index.ts [command]

Commands:
  deploy          Deploy all servers directly to agent configs (default)
  enable <name>   Enable an MCP server and re-deploy to agents
  disable <name>  Disable an MCP server and re-deploy to agents
  list            Show servers with status and secrets
  check           Validate server configurations and that .env.example documents every secret
  test            Execute test suite via bun test
  help            Show this help message
`);
}

export async function main(args: string[] = process.argv.slice(2)): Promise<void> {
  const command = args[0] || "deploy";

  switch (command) {
    case "deploy": {
      const ok = await runDeploy();
      if (!ok) process.exit(1);
      break;
    }
    case "enable": {
      const ok = await runEnable(args[1]);
      if (!ok) process.exit(1);
      break;
    }
    case "disable": {
      const ok = await runDisable(args[1]);
      if (!ok) process.exit(1);
      break;
    }
    case "list": {
      await runList();
      break;
    }
    case "check": {
      const ok = await runCheck();
      if (!ok) process.exit(1);
      break;
    }
    case "test": {
      const proc = Bun.spawn(["bun", "test"], {
        stdio: ["inherit", "inherit", "inherit"]
      });
      const exitCode = await proc.exited;
      process.exit(exitCode);
      break;
    }
    case "help":
    case "--help":
    case "-h": {
      printHelp();
      break;
    }
    default: {
      console.error(`Unknown command: ${command}\n`);
      printHelp();
      process.exit(1);
    }
  }
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(`Fatal error: ${(err as Error).message}`);
    process.exit(1);
  });
}
