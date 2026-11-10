// BM25 ranking over the tools of gateway servers.

export interface ToolEntry {
  server: string;
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface SearchHit extends ToolEntry {
  score: number;
}

const STOP = new Set([
  "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it",
  "of", "on", "or", "that", "the", "this", "to", "use", "when", "with", "you", "your"
]);

export function tokenize(text: string): string[] {
  return text
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2") // camelCase -> camel Case
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 1 && !STOP.has(t))
    .map(singular);
}

/** Crude plural folding so "rate" matches "rates" and "devices" matches "device". */
function singular(token: string): string {
  if (token.length <= 3 || token.endsWith("ss") || token.endsWith("us")) return token;
  if (token.endsWith("ies")) return token.slice(0, -3) + "y";
  return token.endsWith("s") ? token.slice(0, -1) : token;
}

interface Doc {
  entry: ToolEntry;
  tf: Map<string, number>;
  length: number;
  nameTokens: Set<string>;
}

/**
 * Field weights: the tool name says the most about what a tool does, then the
 * server name and description (so "flutter" finds dart tools), then the tool description.
 */
function buildDoc(entry: ToolEntry, serverDescription: string): Doc {
  const weighted: [string[], number][] = [
    [tokenize(entry.name), 3],
    [tokenize(entry.server), 2],
    [tokenize(serverDescription), 1],
    [tokenize(entry.description), 1]
  ];
  const tf = new Map<string, number>();
  let length = 0;
  for (const [tokens, weight] of weighted) {
    for (const t of tokens) {
      tf.set(t, (tf.get(t) ?? 0) + weight);
      length += weight;
    }
  }
  return { entry, tf, length, nameTokens: new Set(tokenize(entry.name)) };
}

export function searchTools(
  entries: ToolEntry[],
  query: string,
  options: { limit?: number; serverDescriptions?: Record<string, string> } = {}
): SearchHit[] {
  const limit = options.limit ?? 5;
  const terms = [...new Set(tokenize(query))];
  if (terms.length === 0 || entries.length === 0) return [];

  const docs = entries.map((e) => buildDoc(e, options.serverDescriptions?.[e.server] ?? ""));
  const avgLength = docs.reduce((sum, d) => sum + d.length, 0) / docs.length;
  const k1 = 1.2;
  const b = 0.75;

  const df = new Map<string, number>();
  for (const term of terms) {
    df.set(term, docs.filter((d) => d.tf.has(term)).length);
  }

  const q = query.trim().toLowerCase();
  const hits: SearchHit[] = [];
  for (const doc of docs) {
    let score = 0;
    for (const term of terms) {
      const freq = doc.tf.get(term);
      if (!freq) continue;
      const n = df.get(term) ?? 0;
      const idf = Math.log(1 + (docs.length - n + 0.5) / (n + 0.5));
      score += idf * ((freq * (k1 + 1)) / (freq + k1 * (1 - b + (b * doc.length) / avgLength)));
      // A query word in the tool's own name is strong evidence, however common the word is.
      if (doc.nameTokens.has(term)) score += idf;
    }
    // An exact tool name in the query always wins.
    if (q === doc.entry.name.toLowerCase()) score += 100;
    if (score > 0) hits.push({ ...doc.entry, score });
  }

  return hits.sort((x, y) => y.score - x.score).slice(0, limit);
}
