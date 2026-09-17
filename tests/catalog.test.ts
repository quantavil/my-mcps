import { describe, expect, test } from "bun:test";
import { END_TAG, START_TAG, renderReadmeTable, updateReadmeContent } from "../src/catalog";

describe("renderReadmeTable", () => {
  test("renderReadmeTable produces formatted markdown table", () => {
    const servers = {
      filesystem: {
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-filesystem"],
        description: "Local filesystem operations"
      }
    };
    const table = renderReadmeTable(servers);
    expect(table).toContain("| `filesystem` | Local filesystem operations | `npx -y @modelcontextprotocol/server-filesystem` | None |");
    expect(table).toContain("| Server | Description | Command | Required Secrets |");
    expect(table).toContain("| --- | --- | --- | --- |");
  });

  test("sorts servers alphabetically and renders required secrets", () => {
    const servers = {
      zebra: {
        command: "run-zebra",
        description: "Zebra service"
      },
      alpha: {
        command: "run-alpha",
        args: ["--port", "8080"],
        env: {
          API_KEY: "${API_KEY}",
          SECRET_TOKEN: "${SECRET_TOKEN}"
        },
        description: "Alpha service"
      }
    };

    const table = renderReadmeTable(servers);
    const lines = table.split("\n");
    // Row 0: header, Row 1: divider, Row 2: alpha, Row 3: zebra
    expect(lines[2]).toContain("| `alpha` | Alpha service | `run-alpha --port 8080` | `API_KEY`, `SECRET_TOKEN` |");
    expect(lines[3]).toContain("| `zebra` | Zebra service | `run-zebra` | None |");
  });

  test("escapes pipe characters in description", () => {
    const servers = {
      pipes: {
        command: "pipes-cmd",
        description: "Handles A | B | C"
      }
    };

    const table = renderReadmeTable(servers);
    expect(table).toContain("Handles A \\| B \\| C");
  });
});

describe("updateReadmeContent", () => {
  test("replaces content between start and end markers", () => {
    const original = `# My Project\n\n${START_TAG}\nold content\n${END_TAG}\n\n## Footer`;
    const table = "| Server | Description |\n| --- | --- |\n| `test` | Test |";
    const updated = updateReadmeContent(original, table);

    expect(updated).toBe(`# My Project\n\n${START_TAG}\n\n${table}\n\n${END_TAG}\n\n## Footer`);
  });

  test("throws error if markers are missing", () => {
    const original = "# My Project\n\nNo markers here\n";
    const table = "| Server | Description |\n| --- | --- |";

    expect(() => updateReadmeContent(original, table)).toThrow("README.md is missing markers");
  });
});
