import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

type LiveApi = "openai-completions" | "google-generative-ai";

interface LiveProvider {
  id: string;
  name: string;
  baseUrl: string;
  authEnv: string;
  curatedFile: string;
  api?: LiveApi;
  fallbackToStored?: boolean;
}

type CatalogFetcher = (cfg: LiveProvider, apiKey: string, signal: AbortSignal) => Promise<RawModel[]>;

interface RawModel {
  id: string;
  name?: string;
  context_length?: number;
  context_window?: number;
  contextWindow?: number;
  max_tokens?: number;
  maxTokens?: number;
}

interface LiveModel {
  id: string;
  name: string;
  api: LiveApi;
  provider: string;
  baseUrl: string;
  reasoning: boolean;
  input: string[];
  cost: { input: number; output: number; cacheRead: number; cacheWrite: number };
  contextWindow: number;
  maxTokens: number;
}

interface RefreshContext {
  credential?: { type: string; key?: string };
  stored?: { models: LiveModel[] };
  allowNetwork: boolean;
  signal: AbortSignal;
  publish(p: { persist?: { models: LiveModel[]; checkedAt: number } | null }): Promise<boolean>;
}

interface PiApi {
  registerProvider(
    name: string,
    config: {
      name: string;
      baseUrl: string;
      apiKey: string;
      api: LiveApi;
      models: LiveModel[];
      refreshModels?(ctx: RefreshContext): Promise<LiveModel[]>;
    }
  ): void;
}

const CURATED_DIR = fileURLToPath(new URL("curated", import.meta.url));
const FETCH_TIMEOUT_MS = 15_000;
const DEFAULT_CONTEXT_WINDOW = 128000;
const DEFAULT_MAX_TOKENS = 4096;

const NON_CHAT_KEYWORDS = new Set([
  "embed", "safety", "guard", "translate", "parse",
  "retriever", "clip", "diffusion", "video", "detector",
  "reward", "deplot",
  "imagen", "veo", "lyria", "tts", "audio",
  "deep-research", "robotics",
]);

function isNonChat(modelId: string): boolean {
  const lower = modelId.toLowerCase();
  return [...NON_CHAT_KEYWORDS].some((kw) => lower.includes(kw));
}

function globToRegExp(pattern: string): RegExp {
  const body = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*/g, "\u0000")
    .replace(/\*/g, "[^/]*")
    .replace(/\u0000/g, ".*");
  return new RegExp(`^${body}$`);
}

function matchesPattern(id: string, pattern: string): boolean {
  if (!pattern.includes("*")) return id === pattern;
  return globToRegExp(pattern).test(id);
}

function readPatterns(curatedFile: string, id: string): string[] {
  let patterns: string[] = [];
  try {
    const parsed = JSON.parse(readFileSync(curatedFile, "utf8")) as { patterns?: unknown };
    if (parsed && Array.isArray(parsed.patterns)) {
      patterns = parsed.patterns.filter((entry) => typeof entry === "string" && entry.length > 0);
    }
  } catch (error) {
    patterns = [];
  }
  if (patterns.length === 0) {
    console.warn(
      `[live:${id}] curated file missing or empty (${curatedFile}); showing no models.`
    );
  }
  return patterns;
}

function toPiModel(cfg: LiveProvider, raw: RawModel): LiveModel {
  const contextWindow = raw.context_length || raw.context_window || raw.contextWindow || DEFAULT_CONTEXT_WINDOW;
  const maxTokens = raw.max_tokens || raw.maxTokens || DEFAULT_MAX_TOKENS;
  return {
    id: raw.id,
    name: raw.name || raw.id,
    api: cfg.api ?? "openai-completions",
    provider: cfg.id,
    baseUrl: cfg.baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow,
    maxTokens,
  };
}

async function fetchCatalog(cfg: LiveProvider, apiKey: string | undefined, signal: AbortSignal): Promise<RawModel[]> {
  const response = await fetch(`${cfg.baseUrl}/models`, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined,
    signal,
  });
  if (!response.ok) {
    throw new Error(`GET ${cfg.baseUrl}/models -> HTTP ${response.status}`);
  }
  const payload = (await response.json()) as { data?: unknown };
  if (!payload || !Array.isArray(payload.data)) {
    throw new Error(`GET ${cfg.baseUrl}/models returned an unexpected payload`);
  }
  return payload.data;
}

async function fetchCatalogGemini(
  cfg: LiveProvider,
  apiKey: string,
  signal: AbortSignal,
): Promise<RawModel[]> {
  const url = `${cfg.baseUrl}/models`;
  const response = await fetch(url, {
    headers: { "x-goog-api-key": apiKey },
    signal,
  });
  if (!response.ok) {
    throw new Error(`GET ${url} -> HTTP ${response.status}`);
  }
  const payload = (await response.json()) as { models?: unknown };
  if (!payload || !Array.isArray(payload.models)) {
    throw new Error(`GET ${url} returned an unexpected payload`);
  }
  const models: RawModel[] = [];
  for (const m of payload.models) {
    if (!m || typeof m !== "object") continue;
    const name = (m as Record<string, unknown>).name;
    if (typeof name !== "string" || !name) continue;
    const modelId = name.replace(/^models\//, "");
    if (!modelId) continue;
    const methods = (m as Record<string, unknown>).supportedGenerationMethods;
    if (!Array.isArray(methods) || !methods.includes("generateContent")) continue;
    if (isNonChat(modelId)) continue;
    const record = m as Record<string, unknown>;
    const inputTokenLimit = record.inputTokenLimit;
    const outputTokenLimit = record.outputTokenLimit;
    models.push({
      id: modelId,
      name: modelId,
      contextWindow: typeof inputTokenLimit === "number" && inputTokenLimit > 0 ? inputTokenLimit : undefined,
      maxTokens: typeof outputTokenLimit === "number" && outputTokenLimit > 0 ? outputTokenLimit : undefined,
    });
  }
  return models;
}

function resolveKey(cfg: LiveProvider, context: RefreshContext): string | undefined {
  if (context.credential && context.credential.type === "api_key" && context.credential.key) {
    return context.credential.key;
  }
  return process.env[cfg.authEnv];
}

function makeRefreshModels(
  cfg: LiveProvider,
  fetcher: CatalogFetcher = fetchCatalog,
): (context: RefreshContext) => Promise<LiveModel[]> {
  return async (context) => {
    if (!context.allowNetwork || context.signal.aborted) {
      return context.stored ? context.stored.models : [];
    }
    const apiKey = resolveKey(cfg, context);
    if (!apiKey) {
      return context.stored ? context.stored.models : [];
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const onAbort = () => controller.abort(context.signal.reason);
    context.signal.addEventListener("abort", onAbort, { once: true });
    try {
      const patterns = readPatterns(cfg.curatedFile, cfg.id);
      const catalog = await fetcher(cfg, apiKey, controller.signal);
      const models = catalog
        .map((raw) => toPiModel(cfg, raw))
        .filter((model) => patterns.some((pattern) => matchesPattern(model.id, pattern)));
      await context.publish({ persist: { models, checkedAt: Date.now() } });
      return models;
    } catch (error) {
      return (cfg.fallbackToStored !== false && context.stored) ? context.stored.models : [];
    } finally {
      clearTimeout(timeout);
      context.signal.removeEventListener("abort", onAbort);
    }
  };
}

const EXT_DIR = fileURLToPath(new URL(".", import.meta.url));

export default (pi: PiApi): void => {
  // Providers are derived from the generated manifest (providers.json), written
  // by `pi-setup auth` from the shared provider_registry — the single source of
  // truth. A missing or unreadable manifest degrades to zero providers rather
  // than throwing at load time.
  let manifest: { providers: LiveProvider[] } = { providers: [] };
  try {
    manifest = JSON.parse(readFileSync(join(EXT_DIR, "providers.json"), "utf8"));
  } catch {
    manifest = { providers: [] };
  }
  for (const p of manifest.providers) {
    const fetcher = p.api === "google-generative-ai" ? fetchCatalogGemini : fetchCatalog;
    // The manifest carries curatedFile as a basename; join it with our own
    // CURATED_DIR so it resolves regardless of the process CWD.
    const cfg: LiveProvider = { ...p, curatedFile: join(CURATED_DIR, p.curatedFile) };
    pi.registerProvider(p.id, {
      name: p.name,
      baseUrl: p.baseUrl,
      apiKey: `$${p.authEnv}`,
      api: p.api ?? "openai-completions",
      models: [],
      refreshModels: makeRefreshModels(cfg, fetcher),
    });
  }
};
