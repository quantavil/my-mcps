import fs from "node:fs";
import path from "node:path";
import { interpolateSecrets, parseEnv } from "./secrets";

export function isEnabled(def: Record<string, any>): boolean {
  return def.enabled !== false;
}

export function loadManifest(rootDir: string): { manifest: any; error?: string } {
  const manifestPath = path.join(rootDir, "mcp-servers.json");
  if (!fs.existsSync(manifestPath)) {
    return { manifest: null, error: `Manifest file not found at ${manifestPath}` };
  }
  try {
    return { manifest: JSON.parse(fs.readFileSync(manifestPath, "utf-8")) };
  } catch (err) {
    return { manifest: null, error: `Invalid JSON in manifest file at ${manifestPath}: ${(err as Error).message}` };
  }
}

/** process.env, then HOME and MCP_ROOT, then the repo's .env (which wins). */
export function buildEnv(rootDir: string, homeDir: string): Record<string, string> {
  const envPath = path.join(rootDir, ".env");
  const fileEnv = fs.existsSync(envPath) ? parseEnv(fs.readFileSync(envPath, "utf-8")) : {};
  return {
    ...(process.env as Record<string, string>),
    HOME: homeDir,
    MCP_ROOT: rootDir,
    ...fileEnv
  };
}

/** Enabled servers with ${VAR} references resolved. */
export function resolveServers(
  servers: Record<string, any>,
  env: Record<string, string>
): { servers: Record<string, any>; missing: string[] } {
  const selected: Record<string, any> = {};
  for (const [name, def] of Object.entries(servers)) {
    if (isEnabled(def)) selected[name] = def;
  }
  return interpolateSecrets(selected, env);
}

/** Validate that each server definition specifies either a command or a url. */
export function validateServers(servers: Record<string, any>): string[] {
  const problems: string[] = [];
  for (const [name, def] of Object.entries(servers)) {
    if (!def.command && !def.url) {
      problems.push(`'${name}' must specify either 'command' or 'url'`);
    }
  }
  return problems;
}
