import fs from "node:fs";
import path from "node:path";
import { interpolateSecrets, parseEnv } from "./secrets";

/**
 * core:    deployed straight into every agent config (always loaded).
 * gateway: hidden behind the single `gateway` server and found with search_tools.
 */
export type Tier = "core" | "gateway";
export const TIERS: readonly Tier[] = ["core", "gateway"];

/** Servers without an explicit tier are treated as core; `check` insists the real manifest sets one. */
export function tierOf(def: Record<string, any>): Tier {
  return def.tier === "gateway" ? "gateway" : "core";
}

export function isEnabled(def: Record<string, any>): boolean {
  return def.enabled !== false;
}

/** Remote (Streamable HTTP) servers have a url instead of a command. */
export function isRemote(def: Record<string, any>): boolean {
  return typeof def.url === "string";
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

/** Enabled servers of the given tier with ${VAR} references resolved. */
export function resolveServers(
  servers: Record<string, any>,
  tier: Tier,
  env: Record<string, string>
): { servers: Record<string, any>; missing: string[] } {
  const selected: Record<string, any> = {};
  for (const [name, def] of Object.entries(servers)) {
    if (isEnabled(def) && tierOf(def) === tier) selected[name] = def;
  }
  return interpolateSecrets(selected, env);
}

/** Problems in the manifest's tier settings, as human-readable lines. */
export function validateTiers(servers: Record<string, any>): string[] {
  const problems: string[] = [];
  for (const [name, def] of Object.entries(servers)) {
    if (def.tier === undefined) {
      problems.push(`'${name}' has no tier (set "tier": "core" or "gateway")`);
    } else if (!TIERS.includes(def.tier)) {
      problems.push(`'${name}' has unknown tier '${def.tier}'`);
    }
    if (isRemote(def) && tierOf(def) !== "gateway") {
      problems.push(`'${name}' is a remote (url) server and must use tier "gateway"`);
    }
    if (name === "gateway") {
      problems.push(`'gateway' is reserved for the gateway itself`);
    }
  }
  return problems;
}
