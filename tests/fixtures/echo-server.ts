// Minimal stdio MCP server used by the gateway tests.
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const server = new Server({ name: "echo", version: "1.0.0" }, { capabilities: { tools: {} } });

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "echo_text",
      description: "Echo the given text back",
      inputSchema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] }
    },
    {
      name: "read_secret",
      description: "Report whether ECHO_TOKEN and FROM_DOTENV are visible to this process",
      inputSchema: { type: "object", properties: {} }
    }
  ]
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "echo_text") {
    return { content: [{ type: "text", text: String(req.params.arguments?.text ?? "") }] };
  }
  if (req.params.name === "read_secret") {
    const text = `token=${process.env.ECHO_TOKEN ?? "none"} dotenv=${process.env.FROM_DOTENV ?? "none"}`;
    return { content: [{ type: "text", text }] };
  }
  return { content: [{ type: "text", text: "unknown tool" }], isError: true };
});

await server.connect(new StdioServerTransport());
