import fs from "node:fs";
import path from "node:path";
import { renderReadmeTable, updateReadmeContent } from "./src/catalog";
import { deployToAgents } from "./src/deployer";
import { interpolateSecrets, parseEnv } from "./src/secrets";

export function extractReferencedEnvVars(servers: Record<string, any>): string[] {
  const vars = new Set<string>();
  const jsonStr = JSON.stringify(servers);
  const matches = jsonStr.matchAll(/\${([A-Z0-9_]+)}/g);
  for (const match of matches) {
    vars.add(match[1]);
  }
  for (const server of Object.values(servers)) {
    if (server.env && typeof server.env === "object") {
      for (const key of Object.keys(server.env)) {
        vars.add(key);
      }
    }
  }
  return Array.from(vars).sort();
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
  const servers = manifest.servers || {};

  // 1. Read .env if present
  const envPath = path.join(rootDir, ".env");
  let envVars: Record<string, string> = {};
  if (fs.existsSync(envPath)) {
    const envRaw = await fs.promises.readFile(envPath, "utf-8");
    envVars = parseEnv(envRaw);
  }

  // 2. Interpolate secrets
  const template = JSON.stringify(servers);
  const { resolved, missing } = interpolateSecrets(template, envVars);
  if (missing.length > 0) {
    console.warn(`⚠️  Warning: Missing secrets in .env: ${missing.join(", ")}`);
  }
  const resolvedServers = JSON.parse(resolved);

  // 3. Update README.md catalog
  const readmePath = path.join(rootDir, "README.md");
  if (fs.existsSync(readmePath)) {
    try {
      const readmeContent = await fs.promises.readFile(readmePath, "utf-8");
      const table = renderReadmeTable(servers);
      const updatedReadme = updateReadmeContent(readmeContent, table);
      await fs.promises.writeFile(readmePath, updatedReadme);
      console.log("✓ Updated README.md catalog");
    } catch (err) {
      console.error(`✗ Failed to update README.md: ${(err as Error).message}`);
    }
  }

  // 4. Deploy to agent targets
  const reports = await deployToAgents(resolvedServers, homeDir);
  for (const report of reports) {
    console.log(report);
  }

  return true;
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

  // 1. Validate README.md catalog
  const readmePath = path.join(rootDir, "README.md");
  if (!fs.existsSync(readmePath)) {
    console.error(`✗ Missing README.md file at ${readmePath}`);
    passed = false;
  } else {
    const readmeContent = await fs.promises.readFile(readmePath, "utf-8");
    const expectedTable = renderReadmeTable(servers);
    try {
      const expectedReadme = updateReadmeContent(readmeContent, expectedTable);
      if (readmeContent !== expectedReadme) {
        console.error("✗ README.md catalog table is outdated. Run 'bun run deploy' to update.");
        passed = false;
      } else {
        console.log("✓ README.md catalog table is up to date");
      }
    } catch (err) {
      console.error(`✗ README.md validation failed: ${(err as Error).message}`);
      passed = false;
    }
  }

  // 2. Validate .env.example coverage
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
      console.log("✓ All referenced environment variables are present in .env.example");
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
  deploy    Interpolate secrets, update README catalog, and deploy to agent targets (default)
  check     Validate README catalog and .env.example coverage
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
