import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { gatewayEntry, runCheck, runDeploy } from "../index";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createGatewayServer, definitionHash, Gateway, gatewayFromManifest, searchToolDescription } from "../src/gateway";
import { resolveServers, tierOf, validateTiers } from "../src/manifest";
import { searchTools, tokenize, type ToolEntry } from "../src/search";

const ECHO = path.join(import.meta.dir, "fixtures/echo-server.ts");

const tool = (server: string, name: string, description: string): ToolEntry => ({
  server,
  name,
  description,
  inputSchema: { type: "object" }
});

describe("search", () => {
  const entries = [
    tool("rea", "inspect_apk_signature", "Inspect the signing certificate of an APK"),
    tool("rea", "list_adb_devices", "List connected Android devices over ADB"),
    tool("dart", "hot_reload", "Hot reload the running Flutter application"),
    tool("dart", "pub_dev_search", "Search pub.dev for packages"),
    tool("svelte", "svelte-autofixer", "Check a Svelte component and suggest fixes")
  ];
  const serverDescriptions = { dart: "Dart and Flutter tooling", rea: "Reverse engineering", svelte: "Svelte docs" };

  test("tokenize splits snake, kebab and camel case and drops stop words", () => {
    expect(tokenize("getFileOutline for the svelte-autofixer")).toEqual(["get", "file", "outline", "svelte", "autofixer"]);
  });

  test("singular and plural forms match", () => {
    expect(tokenize("exchange rates entries devices class status")).toEqual(["exchange", "rate", "entry", "device", "class", "status"]);
  });

  test("ranks the tool that matches the task first", () => {
    expect(searchTools(entries, "apk signature", { serverDescriptions })[0]?.name).toBe("inspect_apk_signature");
    expect(searchTools(entries, "android devices adb", { serverDescriptions })[0]?.name).toBe("list_adb_devices");
  });

  test("server descriptions make a server findable by topic", () => {
    const hits = searchTools(entries, "flutter", { serverDescriptions });
    expect(hits.map((h) => h.server)).toEqual(["dart", "dart"]);
    expect(hits[0]?.name).toBe("hot_reload");
  });

  test("a server description that names its tools does not drown out the tools' own text", () => {
    const india = [
      tool("india", "validate_pan", "Validate Indian PAN format"),
      tool("india", "validate_mobile_number", "Validate an Indian mobile number"),
      tool("india", "calculate_tds", "Calculate TDS for a section and amount"),
      tool("india", "calculate_emi", "Calculate a loan EMI")
    ];
    const serverDescriptions = { india: "Validate GSTIN/PAN/mobile numbers, TDS and EMI calculators" };
    expect(searchTools(india, "validate PAN number", { serverDescriptions })[0]?.name).toBe("validate_pan");
    expect(searchTools(india, "TDS calculation", { serverDescriptions })[0]?.name).toBe("calculate_tds");
  });

  test("exact tool name wins, limit is respected, no match returns nothing", () => {
    expect(searchTools(entries, "pub_dev_search")[0]?.name).toBe("pub_dev_search");
    expect(searchTools(entries, "search", { limit: 1 })).toHaveLength(1);
    expect(searchTools(entries, "kubernetes helm")).toEqual([]);
    expect(searchTools(entries, "   ")).toEqual([]);
  });
});

describe("tiers", () => {
  test("missing tier means core; only enabled servers of a tier resolve", () => {
    expect(tierOf({ command: "x" })).toBe("core");
    const servers = {
      a: { tier: "core", command: "a" },
      b: { tier: "gateway", command: "b" },
      c: { tier: "gateway", command: "c", enabled: false }
    };
    expect(Object.keys(resolveServers(servers, "core", {}).servers)).toEqual(["a"]);
    expect(Object.keys(resolveServers(servers, "gateway", {}).servers)).toEqual(["b"]);
  });

  test("validateTiers flags missing or unknown tiers, core remote servers and the reserved name", () => {
    const problems = validateTiers({
      ok: { tier: "core", command: "x" },
      none: { command: "x" },
      odd: { tier: "sometimes", command: "x" },
      remote: { tier: "core", url: "https://example.com/mcp" },
      gateway: { tier: "core", command: "x" }
    });
    expect(problems).toHaveLength(4);
    expect(problems.join("\n")).toContain("'none' has no tier");
    expect(problems.join("\n")).toContain("'odd' has unknown tier");
    expect(problems.join("\n")).toContain("'remote' is a remote");
    expect(problems.join("\n")).toContain("'gateway' is reserved");
  });

  test("the repo manifest passes the tier check", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(import.meta.dir, "../mcp-servers.json"), "utf-8"));
    expect(validateTiers(manifest.servers)).toEqual([]);
  });

  test("definitionHash ignores description/tier/enabled but tracks the launch definition", () => {
    const base = { command: "bunx", args: ["x"] };
    expect(definitionHash({ ...base, description: "a", tier: "gateway" })).toBe(definitionHash(base));
    expect(definitionHash({ ...base, args: ["y"] })).not.toBe(definitionHash(base));
  });

  test("search_tools description lists every hidden server", () => {
    const text = searchToolDescription({ rea: "Reverse engineering", dart: "Flutter" });
    expect(text).toContain("- rea: Reverse engineering");
    expect(text).toContain("- dart: Flutter");
  });
});

describe("deploy with a gateway", () => {
  let rootDir: string;
  let homeDir: string;

  beforeEach(async () => {
    rootDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-gw-root-"));
    homeDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-gw-home-"));
  });

  afterEach(async () => {
    await fs.promises.rm(rootDir, { recursive: true, force: true });
    await fs.promises.rm(homeDir, { recursive: true, force: true });
  });

  test("core servers deploy directly, gateway servers only through managed-gateway", async () => {
    const servers = {
      web: { tier: "core", command: "bunx", args: ["web"] },
      heavy: { tier: "gateway", command: "bunx", args: ["heavy"], env: { HEAVY_KEY: "${HEAVY_KEY}" } },
      docs: { tier: "gateway", url: "https://example.com/mcp" }
    };
    await fs.promises.writeFile(path.join(rootDir, "mcp-servers.json"), JSON.stringify({ servers }));
    await fs.promises.writeFile(path.join(rootDir, ".env"), "HEAVY_KEY=s3cret\n");

    expect(await runDeploy(rootDir, homeDir)).toBe(true);

    const claude = JSON.parse(await fs.promises.readFile(path.join(homeDir, ".claude.json"), "utf-8"));
    expect(Object.keys(claude.mcpServers).sort()).toEqual(["managed-gateway", "managed-web"]);
    expect(claude.mcpServers["managed-gateway"]).toEqual({ command: "bun", args: [path.join(rootDir, "gateway.ts")] });
    // The gateway server's secret never lands in an agent config.
    expect(await fs.promises.readFile(path.join(homeDir, ".claude.json"), "utf-8")).not.toContain("s3cret");

    const codex = Bun.TOML.parse(await fs.promises.readFile(path.join(homeDir, ".codex/config.toml"), "utf-8")) as any;
    expect(Object.keys(codex.mcp_servers).sort()).toEqual(["managed-gateway", "managed-web"]);
  });

  test("no gateway entry when every gateway server is disabled", () => {
    expect(gatewayEntry({ a: { tier: "core", command: "a" } }, "/r")).toBeUndefined();
    expect(gatewayEntry({ b: { tier: "gateway", command: "b", enabled: false } }, "/r")).toBeUndefined();
    expect(gatewayEntry({ b: { tier: "gateway", command: "b" } }, "/r")?.args).toEqual(["/r/gateway.ts"]);
  });

  test("check fails when a server has no tier", async () => {
    await fs.promises.writeFile(
      path.join(rootDir, "mcp-servers.json"),
      JSON.stringify({ servers: { a: { command: "a" } } })
    );
    await fs.promises.writeFile(path.join(rootDir, ".env.example"), "");
    expect(await runCheck(rootDir)).toBe(false);
  });
});

describe("Gateway against a real stdio server", () => {
  let cacheDir: string;
  let gateway: Gateway;

  const servers = {
    echo: { command: "bun", args: [ECHO], env: { ECHO_TOKEN: "abc" }, description: "Echo test server" }
  };

  beforeEach(async () => {
    cacheDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "mcp-gw-cache-"));
    gateway = new Gateway(servers, { FROM_DOTENV: "x" }, cacheDir);
  });

  afterEach(async () => {
    await gateway.close();
    await fs.promises.rm(cacheDir, { recursive: true, force: true });
  });

  test("indexes, caches, searches and calls", async () => {
    const { hits } = await gateway.search("echo text back");
    expect(hits[0]?.name).toBe("echo_text");
    expect(hits[0]?.inputSchema).toMatchObject({ required: ["text"] });

    const cache = JSON.parse(await fs.promises.readFile(path.join(cacheDir, "echo.json"), "utf-8"));
    expect(cache.tools).toHaveLength(3);
    expect(cache.hash).toBe(definitionHash(servers.echo));

    const result: any = await gateway.call("echo", "echo_text", { text: "hello" });
    expect(result.content[0].text).toBe("hello");
  }, 30_000);

  test("search uses the disk cache without starting the server", async () => {
    await gateway.indexAll();
    await gateway.close();

    const cold = new Gateway(servers, {}, cacheDir);
    const { hits } = await cold.search("echo");
    expect(hits.length).toBeGreaterThan(0);
    expect((cold as any).clients.size).toBe(0);
    await cold.close();
  }, 30_000);

  test("children get their declared env, not other .env secrets", async () => {
    // FROM_DOTENV is only in the gateway's .env view (constructor env), never in process.env.
    const result: any = await gateway.call("echo", "read_secret", {});
    expect(result.content[0].text).toBe("token=abc dotenv=none");
  }, 30_000);

  test("a server that exits is started again on the next call", async () => {
    await gateway.call("echo", "exit_now", {});
    await Bun.sleep(300);
    const result: any = await gateway.call("echo", "echo_text", { text: "back" });
    expect(result.content[0].text).toBe("back");
  }, 30_000);

  test("a failed start reports the server's stderr", async () => {
    const broken = new Gateway(
      { bad: { command: "bun", args: ["-e", "console.error('missing API key'); process.exit(1)"] } },
      {},
      cacheDir
    );
    await expect(broken.call("bad", "x")).rejects.toThrow("missing API key");
    await broken.close();
  }, 30_000);

  test("MCP surface: instructions carry the routing hint, call_tool accepts JSON-string arguments", async () => {
    const server = createGatewayServer(gateway);
    const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
    await server.connect(serverSide);
    const client = new Client({ name: "t", version: "0" });
    await client.connect(clientSide);

    expect(client.getInstructions()).toContain("- echo: Echo test server");
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name)).toEqual(["search_tools", "call_tool"]);

    const result: any = await client.callTool({
      name: "call_tool",
      arguments: { server: "echo", tool: "echo_text", arguments: JSON.stringify({ text: "as string" }) }
    });
    expect(result.content[0].text).toBe("as string");

    const search: any = await client.callTool({ name: "search_tools", arguments: { query: "echo", limit: "two" } });
    expect(search.isError).toBeFalsy();
    expect(search.content[0].text).toContain("## echo / echo_text");
    await client.close();
  }, 30_000);

  test("unknown servers and failed starts are reported, not thrown from indexAll", async () => {
    await expect(gateway.search("x", { server: "nope" })).rejects.toThrow("Unknown server 'nope'");
    const broken = new Gateway({ bad: { command: "/nonexistent/binary" } }, {}, cacheDir);
    expect(await broken.indexAll()).toEqual({});
    expect(broken.errors.has("bad")).toBe(true);
    await broken.close();
  }, 30_000);
});

describe("gatewayFromManifest", () => {
  test("loads only enabled gateway-tier servers from the repo manifest", () => {
    const gw = gatewayFromManifest(path.join(import.meta.dir, ".."), os.homedir());
    const names = Object.keys(gw.servers);
    expect(names).toContain("rea");
    expect(names).not.toContain("firecrawl");
    expect(names).not.toContain("blender");
  });
});
