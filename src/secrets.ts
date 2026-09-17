export function parseEnv(content: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eqIdx = line.indexOf("=");
    if (eqIdx === -1) continue;
    const key = line.slice(0, eqIdx).trim();
    let val = line.slice(eqIdx + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    env[key] = val;
  }
  return env;
}

export function interpolateSecrets(
  template: string,
  env: Record<string, string>
): { resolved: string; missing: string[] } {
  const missing: string[] = [];
  const resolved = template.replace(/\${([A-Z0-9_]+)}/g, (match, varName) => {
    const val = env[varName];
    if (val === undefined || val === "") {
      missing.push(varName);
      return match;
    }
    return val;
  });
  return { resolved, missing: Array.from(new Set(missing)) };
}
