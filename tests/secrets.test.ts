import { expect, test } from "bun:test";
import { interpolateSecrets, parseEnv } from "../src/secrets";

test("parseEnv parses key-value pairs ignoring comments", () => {
  const envText = "FOO=bar\n# comment\nBAZ=qux\n";
  const env = parseEnv(envText);
  expect(env.FOO).toBe("bar");
  expect(env.BAZ).toBe("qux");
  expect(env["# comment"]).toBeUndefined();
});

test("interpolateSecrets replaces placeholders and reports missing keys", () => {
  const template = JSON.stringify({ token: "${MY_SECRET}", fallback: "${NOT_FOUND}" });
  const { resolved, missing } = interpolateSecrets(template, { MY_SECRET: "xyz123" });
  const parsed = JSON.parse(resolved);
  expect(parsed.token).toBe("xyz123");
  expect(missing).toEqual(["NOT_FOUND"]);
});

test("interpolateSecrets handles empty string as missing and deduplicates missing keys", () => {
  const template = "${EMPTY_VAL} and ${EMPTY_VAL} and ${MISSING_VAL}";
  const { resolved, missing } = interpolateSecrets(template, { EMPTY_VAL: "" });
  expect(resolved).toBe("${EMPTY_VAL} and ${EMPTY_VAL} and ${MISSING_VAL}");
  expect(missing).toEqual(["EMPTY_VAL", "MISSING_VAL"]);
});

test("parseEnv strips matching quotes", () => {
  const envText = 'KEY_DOUBLE="hello world"\nKEY_SINGLE=\'single quotes\'\nKEY_PLAIN=plain\n';
  const env = parseEnv(envText);
  expect(env.KEY_DOUBLE).toBe("hello world");
  expect(env.KEY_SINGLE).toBe("single quotes");
  expect(env.KEY_PLAIN).toBe("plain");
});
