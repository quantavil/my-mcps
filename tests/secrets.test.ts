import { expect, test } from "bun:test";
import { interpolateSecrets, parseEnv, substituteString } from "../src/secrets";

test("parseEnv parses key-value pairs ignoring comments", () => {
  const envText = "FOO=bar\n# comment\nBAZ=qux\n";
  const env = parseEnv(envText);
  expect(env.FOO).toBe("bar");
  expect(env.BAZ).toBe("qux");
  expect(env["# comment"]).toBeUndefined();
});

test("parseEnv strips matching quotes", () => {
  const envText = 'KEY_DOUBLE="hello world"\nKEY_SINGLE=\'single quotes\'\nKEY_PLAIN=plain\n';
  const env = parseEnv(envText);
  expect(env.KEY_DOUBLE).toBe("hello world");
  expect(env.KEY_SINGLE).toBe("single quotes");
  expect(env.KEY_PLAIN).toBe("plain");
});

test("substituteString resolves placeholders and reports missing", () => {
  const missing: string[] = [];
  const res = substituteString("prefix/${KEY}/suffix", { KEY: "val" }, (v) => missing.push(v));
  expect(res).toBe("prefix/val/suffix");
  expect(missing).toEqual([]);

  const resMissing = substituteString("${NOT_SET}", {}, (v) => missing.push(v));
  expect(resMissing).toBe("");
  expect(missing).toEqual(["NOT_SET"]);
});

test("interpolateSecrets populates valid env and omits missing keys from deployed server", () => {
  const servers = {
    testServer: {
      command: "npx",
      args: ["-y", "my-pkg", "${HOME}/data"],
      env: {
        TOKEN: "${MY_TOKEN}",
        OPTIONAL_MISSING: "${MISSING_KEY}"
      }
    }
  };

  const { servers: resolved, missing } = interpolateSecrets(servers, {
    HOME: "/test/home",
    MY_TOKEN: "secret-123"
  });

  expect(resolved.testServer.args[2]).toBe("/test/home/data");
  expect(resolved.testServer.env.TOKEN).toBe("secret-123");
  // Crucial: missing key MUST NOT be deployed as literal string "${MISSING_KEY}"
  expect(resolved.testServer.env.OPTIONAL_MISSING).toBeUndefined();
  expect(missing).toEqual(["MISSING_KEY"]);
});
