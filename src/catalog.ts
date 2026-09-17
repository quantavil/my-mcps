export const START_TAG = "<!-- mcp:start -->";
export const END_TAG = "<!-- mcp:end -->";

export function renderReadmeTable(servers: Record<string, any>): string {
  const headers = [
    "| Server | Description | Command | Required Secrets |",
    "| --- | --- | --- | --- |"
  ];

  const rows = Object.entries(servers)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, def]) => {
      const argsStr = def.args && def.args.length > 0 ? ` ${def.args.join(" ")}` : "";
      const cmd = `\`${def.command}${argsStr}\``;
      const desc = (def.description || "").replace(/\|/g, "\\|");
      const envVars = Object.keys(def.env || {});
      const secrets = envVars.length > 0 ? envVars.map((v) => `\`${v}\``).join(", ") : "None";
      return `| \`${name}\` | ${desc} | ${cmd} | ${secrets} |`;
    });

  return [...headers, ...rows].join("\n");
}

export function updateReadmeContent(readme: string, table: string): string {
  const re = new RegExp(`${START_TAG}[\\s\\S]*?${END_TAG}`);
  if (!re.test(readme)) {
    throw new Error(`README.md is missing markers ${START_TAG} and ${END_TAG}`);
  }
  return readme.replace(re, `${START_TAG}\n\n${table}\n\n${END_TAG}`);
}
