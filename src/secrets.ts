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

export function substituteString(
  text: string,
  env: Record<string, string>,
  onMissing?: (varName: string) => void
): string {
  return text.replace(/\${([A-Z0-9_]+)}/g, (match, varName) => {
    const val = env[varName] ?? process.env[varName];
    if (val === undefined || val === "") {
      if (onMissing) onMissing(varName);
      return "";
    }
    return val;
  });
}

export interface InterpolationResult {
  servers: Record<string, any>;
  missing: string[];
}

export function interpolateSecrets(
  servers: Record<string, any>,
  env: Record<string, string>
): InterpolationResult {
  const missing = new Set<string>();
  const resolvedServers: Record<string, any> = {};

  for (const [name, def] of Object.entries(servers)) {
    const serverCopy = JSON.parse(JSON.stringify(def));

    // Interpolate command and args
    if (typeof serverCopy.command === "string") {
      serverCopy.command = substituteString(serverCopy.command, env, (v) => missing.add(v));
    }
    if (Array.isArray(serverCopy.args)) {
      serverCopy.args = serverCopy.args.map((arg: string) =>
        substituteString(arg, env, (v) => missing.add(v))
      );
    }

    // Interpolate environment variables
    if (serverCopy.env && typeof serverCopy.env === "object") {
      const resolvedEnv: Record<string, string> = {};
      for (const [k, v] of Object.entries(serverCopy.env)) {
        if (typeof v === "string") {
          let hasMissing = false;
          const substituted = v.replace(/\${([A-Z0-9_]+)}/g, (match, varName) => {
            const val = env[varName] ?? process.env[varName];
            if (val === undefined || val === "") {
              missing.add(varName);
              hasMissing = true;
              return "";
            }
            return val;
          });

          // Only keep valid populated env vars; never deploy broken literal placeholders
          if (!hasMissing && substituted !== "") {
            resolvedEnv[k] = substituted;
          }
        }
      }

      if (Object.keys(resolvedEnv).length > 0) {
        serverCopy.env = resolvedEnv;
      } else {
        delete serverCopy.env;
      }
    }

    resolvedServers[name] = serverCopy;
  }

  return {
    servers: resolvedServers,
    missing: Array.from(missing).sort()
  };
}
