import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { deployToAgents } from "./src/deployer";
import { GATEWAY_TOOL_TIMEOUT_SEC, gatewayFromManifest } from "./src/gateway";
import { buildEnv, isEnabled, loadManifest, resolveServers, tierOf, validateTiers } from "./src/manifest";
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

/** The agent-facing entry for the gateway, or undefined when no gateway servers are enabled. */
export function gatewayEntry(servers: Record<string, any>, rootDir: string): Record<string, any> | undefined {
  const hidden = Object.entries(servers)
    .filter(([, def]) => isEnabled(def) && tierOf(def) === "gateway")
    .map(([name]) => name);
  if (hidden.length === 0) return undefined;
  return {
    command: "bun",
    args: [path.join(rootDir, "gateway.ts")],
    toolTimeoutSec: GATEWAY_TOOL_TIMEOUT_SEC,
    description: `Gateway for ${hidden.join(", ")}`
  };
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

  // Core servers go into agent configs directly. Gateway servers stay out:
  // the gateway reads them (and their secrets) from this repo at runtime.
  const { servers: deployed, missing } = resolveServers(allServers, "core", env);
  if (missing.length > 0) {
    console.warn(`⚠️  Warning: Missing secrets (omitted from deployed servers): ${missing.join(", ")}`);
  }
  const gateway = gatewayEntry(allServers, rootDir);
  if (gateway) deployed.gateway = gateway;

  const reports = await deployToAgents(deployed, homeDir);
  for (const report of reports) {
    console.log(report);
  }
  if (gateway) {
    console.log(`ℹ ${gateway.description}`);
  }

  return true;
}

export async function runIndex(
  args: string[] = [],
  rootDir: string = import.meta.dir,
  homeDir: string = os.homedir()
): Promise<boolean> {
  const refresh = args.includes("--refresh");
  const only = args.filter((a) => !a.startsWith("--"));
  const gateway = gatewayFromManifest(rootDir, homeDir);
  try {
    const counts = await gateway.indexAll({ refresh, only: only.length ? only : undefined });
    for (const [name, count] of Object.entries(counts).sort()) {
      console.log(`✓ ${name}: ${count} tools`);
    }
    for (const [name, message] of gateway.errors) {
      console.error(`✗ ${name}: ${message}`);
    }
    return gateway.errors.size === 0;
  } finally {
    await gateway.close();
  }
}

export async function runSearch(
  args: string[] = [],
  rootDir: string = import.meta.dir,
  homeDir: string = os.homedir()
): Promise<boolean> {
  const query = args.join(" ").trim();
  if (!query) {
    console.error("✗ Usage: ./run.sh search <query>");
    return false;
  }
  const gateway = gatewayFromManifest(rootDir, homeDir);
  try {
    const { hits, errors } = await gateway.search(query, { limit: 8 });
    for (const error of errors) console.error(`✗ ${error}`);
    for (const hit of hits) {
      const desc = hit.description.replace(/\s+/g, " ").slice(0, 100);
      console.log(`${hit.score.toFixed(2).padStart(6)}  ${hit.server}/${hit.name}  ${desc}`);
    }
    if (hits.length === 0) console.log("No matches.");
    return true;
  } finally {
    await gateway.close();
  }
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
    const tierTag = `[${tierOf(d)}]`;
    const cmd = d.url ? d.url : `${d.command} ${(d.args || []).join(" ")}`;
    const envKeys = d.env ? Object.keys(d.env) : [];
    console.log(`• ${bold(name)} ${statusTag} ${tierTag}: ${d.description || ""}`);
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

  const tierProblems = validateTiers(servers);
  if (tierProblems.length > 0) {
    for (const problem of tierProblems) console.error(`✗ ${problem}`);
    passed = false;
  } else {
    console.log("✓ Every server has a valid tier");
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
  deploy          Deploy to agent configs and cache new gateway tool lists (default)
  enable <name>   Enable an MCP server and re-deploy to agents
  disable <name>  Disable an MCP server and re-deploy to agents
  index [names]   Cache the tool lists of gateway servers (--refresh to re-read)
  search <query>  Search gateway tools the way an agent would
  list            Show servers with tier, status and secrets
  check           Validate tiers and that .env.example documents every secret
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
      // Cache tool lists the gateway does not know yet, so agent searches never cold-start servers.
      // A failure here is reported but does not fail the deploy.
      await runIndex();
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
    case "index": {
      const ok = await runIndex(args.slice(1));
      if (!ok) process.exit(1);
      break;
    }
    case "search": {
      const ok = await runSearch(args.slice(1));
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
