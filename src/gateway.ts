// One MCP server that stands in for every "gateway" tier server.
// The agent sees two tools (search_tools, call_tool) instead of every hidden server's tool list.
// Tool lists are cached on disk, so searching does not start servers;
// a server starts on its first call and stays up for the session.

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { buildEnv, loadManifest, resolveServers } from "./manifest";
import { searchTools, type ToolEntry } from "./search";
import { substituteString } from "./secrets";

const CONNECT_TIMEOUT_MS = 120_000; // first bunx/uvx run downloads the package
const CALL_TIMEOUT_MS = 15 * 60_000; // rea analyses and flutter builds are slow
const MAX_LIMIT = 15;
const STDERR_TAIL = 2000;

export function defaultCacheDir(homeDir: string = os.homedir()): string {
  const base = process.env.XDG_CACHE_HOME || path.join(homeDir, ".cache");
  return path.join(base, "my-mcps", "tools");
}

/** Cache key: changes when the server definition changes. Secrets are hashed, never stored. */
export function definitionHash(def: Record<string, any>): string {
  const { description, tier, enabled, ...launch } = def;
  return crypto.createHash("sha256").update(JSON.stringify(launch)).digest("hex").slice(0, 16);
}

interface CacheFile {
  hash: string;
  indexedAt: string;
  tools: ToolEntry[];
}

function withTimeout<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms / 1000}s`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

export class Gateway {
  private clients = new Map<string, Promise<Client>>();
  private indexes = new Map<string, ToolEntry[]>();
  readonly errors = new Map<string, string>();

  constructor(
    readonly servers: Record<string, any>,
    readonly env: Record<string, string>,
    readonly cacheDir: string = defaultCacheDir()
  ) {}

  descriptions(): Record<string, string> {
    return Object.fromEntries(
      Object.entries(this.servers).map(([name, def]) => [name, String(def.description ?? "")])
    );
  }

  private cachePath(name: string): string {
    return path.join(this.cacheDir, `${name}.json`);
  }

  private readCache(name: string): ToolEntry[] | undefined {
    try {
      const cache = JSON.parse(fs.readFileSync(this.cachePath(name), "utf-8")) as CacheFile;
      if (cache.hash === definitionHash(this.servers[name])) return cache.tools;
    } catch {}
    return undefined;
  }

  private writeCache(name: string, tools: ToolEntry[]): void {
    fs.mkdirSync(this.cacheDir, { recursive: true });
    const cache: CacheFile = {
      hash: definitionHash(this.servers[name]),
      indexedAt: new Date().toISOString(),
      tools
    };
    fs.writeFileSync(this.cachePath(name), JSON.stringify(cache, null, 2) + "\n");
  }

  private async connect(name: string): Promise<Client> {
    const def = this.servers[name];
    if (!def) throw new Error(`Unknown server '${name}'. Known: ${Object.keys(this.servers).join(", ")}`);

    const client = new Client({ name: "my-mcps-gateway", version: "1.0.0" });
    let stderrTail = "";
    let transport: StdioClientTransport | StreamableHTTPClientTransport;
    if (typeof def.url === "string") {
      const headers: Record<string, string> = {};
      for (const [k, v] of Object.entries(def.headers ?? {})) {
        headers[k] = substituteString(String(v), this.env);
      }
      transport = new StreamableHTTPClientTransport(new URL(substituteString(def.url, this.env)), {
        requestInit: { headers }
      });
    } else {
      transport = new StdioClientTransport({
        command: def.command,
        args: def.args ?? [],
        // The agent's own environment plus this server's declared env: other .env secrets stay out.
        env: { ...(process.env as Record<string, string>), ...(def.env ?? {}) },
        stderr: "pipe" // never inherit: our stdout/stderr belong to the agent
      });
      transport.stderr?.on("data", (chunk: Buffer) => {
        stderrTail = (stderrTail + chunk.toString()).slice(-STDERR_TAIL);
      });
    }

    try {
      await withTimeout(client.connect(transport), CONNECT_TIMEOUT_MS, `Starting '${name}'`);
    } catch (err) {
      await transport.close().catch(() => {}); // do not leave a half-started child behind
      const detail = stderrTail.trim() ? `: ${stderrTail.trim().split("\n").slice(-3).join(" | ")}` : "";
      throw new Error(`${(err as Error).message}${detail}`);
    }
    // A server that exits later is started again on the next call.
    client.onclose = () => this.clients.delete(name);
    return client;
  }

  /** Connected client for a server, started on first use and reused afterwards. */
  client(name: string): Promise<Client> {
    let pending = this.clients.get(name);
    if (!pending) {
      pending = this.connect(name);
      pending.catch(() => this.clients.delete(name)); // allow a retry after a failed start
      this.clients.set(name, pending);
    }
    return pending;
  }

  private async fetchTools(name: string): Promise<ToolEntry[]> {
    const client = await this.client(name);
    const tools: ToolEntry[] = [];
    let cursor: string | undefined;
    do {
      const page = await client.listTools(cursor ? { cursor } : {});
      for (const t of page.tools) {
        tools.push({
          server: name,
          name: t.name,
          description: t.description ?? "",
          inputSchema: t.inputSchema as Record<string, unknown>
        });
      }
      cursor = page.nextCursor;
    } while (cursor);
    return tools;
  }

  /** Tools of one server: memory, then disk cache, then the live server. */
  async tools(name: string, options: { refresh?: boolean } = {}): Promise<ToolEntry[]> {
    if (!options.refresh) {
      const known = this.indexes.get(name) ?? this.readCache(name);
      if (known) {
        this.indexes.set(name, known);
        return known;
      }
    }
    const tools = await this.fetchTools(name);
    this.writeCache(name, tools);
    this.indexes.set(name, tools);
    this.errors.delete(name);
    return tools;
  }

  /** Index every server, recording failures instead of throwing. */
  async indexAll(options: { refresh?: boolean; only?: string[] } = {}): Promise<Record<string, number>> {
    const names = options.only ?? Object.keys(this.servers);
    const counts: Record<string, number> = {};
    await Promise.all(
      names.map(async (name) => {
        try {
          counts[name] = (await this.tools(name, options)).length;
        } catch (err) {
          this.errors.set(name, (err as Error).message);
        }
      })
    );
    return counts;
  }

  async search(query: string, options: { server?: string; limit?: number } = {}) {
    const names = options.server ? [options.server] : Object.keys(this.servers);
    if (options.server && !this.servers[options.server]) {
      throw new Error(`Unknown server '${options.server}'. Known: ${Object.keys(this.servers).join(", ")}`);
    }
    await this.indexAll({ only: names });
    const entries = names.flatMap((n) => this.indexes.get(n) ?? []);
    const requested = Number(options.limit);
    const limit = Number.isFinite(requested) ? Math.min(Math.max(Math.trunc(requested), 1), MAX_LIMIT) : 5;
    const hits = searchTools(entries, query, { limit, serverDescriptions: this.descriptions() });
    const errors = names.filter((n) => this.errors.has(n)).map((n) => `${n}: ${this.errors.get(n)}`);
    return { hits, errors };
  }

  async call(server: string, tool: string, args: Record<string, unknown> = {}) {
    const firstStart = !this.clients.has(server);
    const client = await this.client(server);
    if (firstStart) {
      // The server is running anyway: re-read its tools so the cache follows upstream updates.
      this.tools(server, { refresh: true }).catch(() => {});
    }
    return client.callTool({ name: tool, arguments: args }, undefined, {
      timeout: CALL_TIMEOUT_MS,
      resetTimeoutOnProgress: true
    });
  }

  async close(): Promise<void> {
    const pending = [...this.clients.values()];
    this.clients.clear();
    await Promise.allSettled(pending.map(async (p) => (await p).close()));
  }
}

function formatHits({ hits, errors }: Awaited<ReturnType<Gateway["search"]>>): string {
  const lines: string[] = [];
  if (hits.length === 0) {
    lines.push("No matching tools. Try other words, or pass `server` to list that server's best matches.");
  }
  for (const hit of hits) {
    lines.push(`## ${hit.server} / ${hit.name}`);
    if (hit.description) lines.push(hit.description.trim());
    lines.push("Input schema: " + JSON.stringify(hit.inputSchema));
    lines.push("");
  }
  if (errors.length > 0) {
    lines.push("Not searchable right now (failed to start):");
    for (const error of errors) lines.push(`- ${error}`);
  }
  return lines.join("\n").trim();
}

/** Routing hint: used as both the server instructions and the search_tools description,
 * because some clients show only one of them up front (Claude Code defers tool descriptions). */
export function searchToolDescription(descriptions: Record<string, string>): string {
  const servers = Object.entries(descriptions)
    .map(([name, desc]) => `- ${name}: ${desc}`)
    .join("\n");
  return [
    "Find tools on MCP servers that are not loaded directly. Search whenever a task touches one of these servers,",
    "then run the tool with call_tool. Results include each tool's input schema.",
    "",
    "Servers:",
    servers
  ].join("\n");
}

export function createGatewayServer(gateway: Gateway): Server {
  const descriptions = gateway.descriptions();
  const serverNames = Object.keys(descriptions);
  const server = new Server(
    { name: "my-mcps-gateway", version: "1.0.0" },
    {
      capabilities: { tools: {} },
      instructions: searchToolDescription(descriptions)
    }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "search_tools",
        description: searchToolDescription(descriptions),
        inputSchema: {
          type: "object",
          properties: {
            query: { type: "string", description: "What you want to do, in a few words" },
            server: { type: "string", enum: serverNames, description: "Only search this server" },
            limit: { type: "number", description: `Results to return (default 5, max ${MAX_LIMIT})` }
          },
          required: ["query"]
        }
      },
      {
        name: "call_tool",
        description: "Run a tool found with search_tools. Pass its server, its name, and arguments matching its input schema.",
        inputSchema: {
          type: "object",
          properties: {
            server: { type: "string", enum: serverNames },
            tool: { type: "string" },
            arguments: { type: "object", description: "Arguments for the tool" }
          },
          required: ["server", "tool"]
        }
      }
    ]
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const args = (request.params.arguments ?? {}) as Record<string, any>;
    try {
      if (request.params.name === "search_tools") {
        if (typeof args.query !== "string" || !args.query.trim()) throw new Error("query is required");
        const result = await gateway.search(args.query, { server: args.server, limit: args.limit });
        return { content: [{ type: "text", text: formatHits(result) }] };
      }
      if (request.params.name === "call_tool") {
        if (typeof args.server !== "string" || typeof args.tool !== "string") {
          throw new Error("server and tool are required");
        }
        // Some models send arguments as a JSON string instead of an object.
        const toolArgs = typeof args.arguments === "string" ? JSON.parse(args.arguments) : args.arguments ?? {};
        return (await gateway.call(args.server, args.tool, toolArgs)) as any;
      }
      throw new Error(`Unknown tool '${request.params.name}'`);
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${(err as Error).message}` }], isError: true };
    }
  });

  return server;
}

/** Build a Gateway from the repo's manifest and .env. */
export function gatewayFromManifest(rootDir: string, homeDir: string = os.homedir()): Gateway {
  const { manifest, error } = loadManifest(rootDir);
  if (!manifest) throw new Error(error);
  const env = buildEnv(rootDir, homeDir);
  const { servers } = resolveServers(manifest.servers ?? {}, "gateway", env);
  return new Gateway(servers, env, defaultCacheDir(homeDir));
}

export async function runGatewayStdio(rootDir: string): Promise<void> {
  const gateway = gatewayFromManifest(rootDir);
  const server = createGatewayServer(gateway);
  const shutdown = async () => {
    await gateway.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  process.stdin.on("end", shutdown);
  await server.connect(new StdioServerTransport());
}
