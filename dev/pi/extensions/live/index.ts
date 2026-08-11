import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

interface LiveProvider {
  id: string;
  name: string;
  baseUrl: string;
  authEnv: string;
  curatedFile: string;
}

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
  api: "openai-completions";
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
      api: "openai-completions";
      models: LiveModel[];
      refreshModels?(ctx: RefreshContext): Promise<LiveModel[]>;
    }
  ): void;
}

const CURATED_DIR = fileURLToPath(new URL("curated", import.meta.url));
const FETCH_TIMEOUT_MS = 15_000;
const DEFAULT_CONTEXT_WINDOW = 128000;
const DEFAULT_MAX_TOKENS = 4096;

const OPENCODE_PROVIDER: LiveProvider = {
  id: "opencode",
  name: "OpenCode Zen",
  baseUrl: "https://opencode.ai/zen/v1",
  authEnv: "OPENCODE_API_KEY",
  curatedFile: join(CURATED_DIR, "opencode.json"),
};

const NIM_PROVIDER: LiveProvider = {
  id: "nim",
  name: "NVIDIA NIM (live)",
  baseUrl: "https://integrate.api.nvidia.com/v1",
  authEnv: "NVIDIA_NIM_API_KEY",
  curatedFile: join(CURATED_DIR, "nim.json"),
};

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
    api: "openai-completions",
    provider: cfg.id,
    baseUrl: cfg.baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow,
    maxTokens,
  };
}

function toStaticModel(id: string): LiveModel {
  return {
    id,
    name: id,
    api: "openai-completions",
    provider: OPENCODE_PROVIDER.id,
    baseUrl: OPENCODE_PROVIDER.baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: DEFAULT_CONTEXT_WINDOW,
    maxTokens: DEFAULT_MAX_TOKENS,
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

function resolveKey(cfg: LiveProvider, context: RefreshContext): string | undefined {
  if (context.credential && context.credential.type === "api_key" && context.credential.key) {
    return context.credential.key;
  }
  return process.env[cfg.authEnv];
}

function makeRefreshModels(cfg: LiveProvider): (context: RefreshContext) => Promise<LiveModel[]> {
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
      const catalog = await fetchCatalog(cfg, apiKey, controller.signal);
      const models = catalog
        .map((raw) => toPiModel(cfg, raw))
        .filter((model) => patterns.some((pattern) => matchesPattern(model.id, pattern)));
      await context.publish({ persist: { models, checkedAt: Date.now() } });
      return models;
    } catch (error) {
      return context.stored ? context.stored.models : [];
    } finally {
      clearTimeout(timeout);
      context.signal.removeEventListener("abort", onAbort);
    }
  };
}

export default (pi: PiApi): void => {
  const zenModels = readPatterns(OPENCODE_PROVIDER.curatedFile, OPENCODE_PROVIDER.id).map(toStaticModel);
  pi.registerProvider(OPENCODE_PROVIDER.id, {
    name: OPENCODE_PROVIDER.name,
    baseUrl: OPENCODE_PROVIDER.baseUrl,
    apiKey: `$${OPENCODE_PROVIDER.authEnv}`,
    api: "openai-completions",
    models: zenModels,
  });
  pi.registerProvider(NIM_PROVIDER.id, {
    name: NIM_PROVIDER.name,
    baseUrl: NIM_PROVIDER.baseUrl,
    apiKey: `$${NIM_PROVIDER.authEnv}`,
    api: "openai-completions",
    models: [],
    refreshModels: makeRefreshModels(NIM_PROVIDER),
  });
};
