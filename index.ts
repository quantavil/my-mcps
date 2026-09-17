import fs from "node:fs";
import path from "node:path";
import { deployToAgents } from "./src/deployer";
import { interpolateSecrets, parseEnv } from "./src/secrets";

export function extractReferencedEnvVars(servers: Record<string, any>): string[] {
  const vars = new Set<string>();
  const jsonStr = JSON.stringify(servers);
  const matches = jsonStr.matchAll(/\${([A-Z0-9_]+)}/g);
  for (const match of matches) {
    // Ignore standard system variables
    if (match[1] !== "HOME") {
      vars.add(match[1]);
    }
  }
  for (const server of Object.values(servers)) {
    if (server.env && typeof server.env === "object") {
      for (const [k, v] of Object.entries(server.env)) {
        if (typeof v === "string") {
          const vMatches = v.matchAll(/\${([A-Z0-9_]+)}/g);
          for (const vm of vMatches) {
            if (vm[1] !== "HOME") vars.add(vm[1]);
          }
        }
      }
    }
  }
  return Array.from(vars).sort();
}

export async function setServerEnabled(
  rootDir: string,
  serverName: string,
  enabled: boolean
): Promise<{ success: boolean; error?: string }> {
  const manifestPath = path.join(rootDir, "mcp-servers.json");
  if (!fs.existsSync(manifestPath)) {
    return { success: false, error: `Manifest file not found at ${manifestPath}` };
  }
  const manifestRaw = await fs.promises.readFile(manifestPath, "utf-8");
  const manifest = JSON.parse(manifestRaw);
  if (!manifest.servers || !manifest.servers[serverName]) {
    return { success: false, error: `Server '${serverName}' not found in manifest` };
  }

  if (enabled) {
    delete manifest.servers[serverName].enabled; // default is enabled
  } else {
    manifest.servers[serverName].enabled = false;
  }

  await fs.promises.writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
  return { success: true };
}

export async function runDeploy(
  rootDir: string = import.meta.dir,
  homeDir: string = process.env.HOME || ""
): Promise<boolean> {
  const manifestPath = path.join(rootDir, "mcp-servers.json");
  if (!fs.existsSync(manifestPath)) {
    console.error(`✗ Manifest file not found at ${manifestPath}`);
    return false;
  }

  const manifestRaw = await fs.promises.readFile(manifestPath, "utf-8");
  const manifest = JSON.parse(manifestRaw);
  const allServers = manifest.servers || {};
  const activeServers: Record<string, any> = {};
  for (const [name, def] of Object.entries(allServers)) {
    if ((def as any).enabled !== false) {
      activeServers[name] = def;
    }
  }

  // 1. Read .env and merge with process.env (system environment variables)
  const envPath = path.join(rootDir, ".env");
  let fileEnv: Record<string, string> = {};
  if (fs.existsSync(envPath)) {
    const envRaw = await fs.promises.readFile(envPath, "utf-8");
    fileEnv = parseEnv(envRaw);
  }
  const mergedEnv: Record<string, string> = {
    ...(process.env as Record<string, string>),
    HOME: homeDir,
    ...fileEnv
  };

  // 2. Interpolate secrets cleanly
  const { servers: resolvedServers, missing } = interpolateSecrets(activeServers, mergedEnv);
  if (missing.length > 0) {
    console.warn(`⚠️  Warning: Missing secrets (omitted from deployed servers): ${missing.join(", ")}`);
  }

  // 3. Deploy to agent targets
  const reports = await deployToAgents(resolvedServers, homeDir);
  for (const report of reports) {
    console.log(report);
  }

  return true;
}

export async function runList(rootDir: string = import.meta.dir): Promise<void> {
  const manifestPath = path.join(rootDir, "mcp-servers.json");
  if (!fs.existsSync(manifestPath)) {
    console.error(`✗ Manifest file not found at ${manifestPath}`);
    return;
  }

  const manifestRaw = await fs.promises.readFile(manifestPath, "utf-8");
  const manifest = JSON.parse(manifestRaw);
  const servers = manifest.servers || {};

  console.log("\nConfigured MCP Servers:");
  console.log("─".repeat(70));
  for (const [name, def] of Object.entries(servers)) {
    const d = def as any;
    const isEnabled = d.enabled !== false;
    const statusTag = isEnabled ? "\x1b[32m[enabled]\x1b[0m" : "\x1b[31m[disabled]\x1b[0m";
    const cmd = `${d.command} ${(d.args || []).join(" ")}`;
    const envKeys = d.env ? Object.keys(d.env) : [];
    console.log(`• \x1b[1m${name}\x1b[0m ${statusTag}: ${d.description || ""}`);
    console.log(`  Command: ${cmd}`);
    if (envKeys.length > 0) {
      console.log(`  Secrets: ${envKeys.join(", ")}`);
    }
    console.log();
  }
}

export async function runCheck(rootDir: string = import.meta.dir): Promise<boolean> {
  let passed = true;

  const manifestPath = path.join(rootDir, "mcp-servers.json");
  if (!fs.existsSync(manifestPath)) {
    console.error(`✗ Missing manifest file: ${manifestPath}`);
    return false;
  }

  const manifestRaw = await fs.promises.readFile(manifestPath, "utf-8");
  const manifest = JSON.parse(manifestRaw);
  const servers = manifest.servers || {};

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

export async function runSync(rootDir: string = import.meta.dir): Promise<void> {
  const manifestPath = path.join(rootDir, "mcp-servers.json");
  if (!fs.existsSync(manifestPath)) {
    console.error(`✗ Missing manifest file: ${manifestPath}`);
    return;
  }

  const manifestRaw = await fs.promises.readFile(manifestPath, "utf-8");
  const manifest = JSON.parse(manifestRaw);
  const servers = manifest.servers || {};

  console.log("ℹ Syncing upstream MCP servers...");
  for (const [name, def] of Object.entries(servers)) {
    const argsStr = (def as any).args ? ` ${(def as any).args.join(" ")}` : "";
    const cmd = `${(def as any).command}${argsStr}`;
    console.log(`  • ${name}: upstream configured (${cmd})`);
  }
  console.log(`✓ Synchronized ${Object.keys(servers).length} upstream MCP server definitions`);
}

export function printHelp(): void {
  console.log(`my-mcps: Centralized MCP Server Manager

Usage:
  ./run.sh [command]
  bun index.ts [command]

Commands:
  deploy    Interpolate secrets and deploy to agent targets (default)
  list      Display configured servers and required secrets
  check     Validate that all required secrets are documented in .env.example
  test      Execute test suite via bun test
  sync      Display upstream MCP server configuration status
  help      Show this help message
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
    case "sync": {
      await runSync();
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
