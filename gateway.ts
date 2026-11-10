#!/usr/bin/env bun
// Entry point that agents launch as the `managed-gateway` MCP server.
import { runGatewayStdio } from "./src/gateway";

runGatewayStdio(import.meta.dir).catch((err) => {
  console.error(`my-mcps gateway: ${(err as Error).message}`);
  process.exit(1);
});
