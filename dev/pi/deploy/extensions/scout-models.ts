import { existsSync, readFileSync, unwatchFile, watchFile } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type {
  ExtensionAPI,
  ModelDefinition,
  ProviderConfig,
} from "@earendil-works/pi-coding-agent";

const PROVIDER_ID = "scout";
const BASE_URL = "http://127.0.0.1:4000/v1";
const DISPLAY_PATH = join(homedir(), ".local", "state", "scout", "catalog.display.json");
const POLL_INTERVAL_MS = 30_000;

interface ScoutEntry {
  readonly id: string;
  readonly badge: string;
  readonly contextWindow?: number;
  readonly maxTokens?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseScoutEntries(raw: string): ScoutEntry[] {
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) return [];

  const entries: ScoutEntry[] = [];
  for (const item of parsed) {
    if (!isRecord(item)) continue;
    const id = item.id;
    if (typeof id !== "string") continue;
    const badge = item.badge;
    const contextWindow = item.contextWindow;
    const maxTokens = item.maxTokens;
    entries.push({
      id,
      badge: typeof badge === "string" ? badge : "",
      ...(typeof contextWindow === "number" && Number.isFinite(contextWindow) && contextWindow > 0
        ? { contextWindow }
        : {}),
      ...(typeof maxTokens === "number" && Number.isFinite(maxTokens) && maxTokens > 0
        ? { maxTokens }
        : {}),
    });
  }
  return entries;
}

function formatTokenCount(count: number): string {
  if (count >= 1_000_000) {
    const millions = count / 1_000_000;
    const remainder = count % 1_000_000;
    if (remainder < 100_000) {
      return `${Math.floor(millions)}M`;
    }
    const formatted = millions.toFixed(1);
    return `${formatted.endsWith(".0") ? formatted.slice(0, -2) : formatted}M`;
  }
  if (count >= 1_000) {
    const thousands = count / 1_000;
    return thousands % 1 === 0 ? `${thousands}K` : `${thousands.toFixed(1)}K`;
  }
  return count.toString();
}

function toModel(entry: ScoutEntry): ModelDefinition {
  const slash = entry.id.indexOf("/");
  const modelId = slash === -1 ? entry.id : entry.id.slice(slash + 1);
  const badgeLabel = entry.badge.length > 0 ? ` [${entry.badge}]` : "";
  const contextWindow =
    typeof entry.contextWindow === "number" && entry.contextWindow > 0
      ? entry.contextWindow
      : 128_000;
  const maxTokens =
    typeof entry.maxTokens === "number" && entry.maxTokens > 0 ? entry.maxTokens : 32_768;
  const ctxLabel =
    typeof entry.contextWindow === "number" && entry.contextWindow > 0
      ? ` [${formatTokenCount(entry.contextWindow)}]`
      : "";

  return {
    id: entry.id,
    name: `${modelId}${badgeLabel}${ctxLabel}`,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow,
    maxTokens,
  };
}

function loadScoutModels(): ModelDefinition[] {
  if (!existsSync(DISPLAY_PATH)) return [];
  try {
    return parseScoutEntries(readFileSync(DISPLAY_PATH, "utf8")).map(toModel);
  } catch {
    return [];
  }
}

function buildProvider(models: ModelDefinition[]): ProviderConfig {
  return {
    name: "Scout (free models)",
    baseUrl: BASE_URL,
    apiKey: "!grep '^LITELLM_MASTER_KEY=' ~/.config/scout/scout.env | cut -d= -f2-",
    api: "openai-completions",
    models,
    refreshModels: async () => loadScoutModels(),
  };
}

export default function (pi: ExtensionAPI): void {
  // Initial catalog: the factory runs before session_start, so models are
  // available to the model picker and `pi --list-models` immediately.
  pi.registerProvider(PROVIDER_ID, buildProvider(loadScoutModels()));

  let watching = false;

  const reload = (): void => {
    // Scout owns the catalog; re-registering replaces the provider's model
    // list without touching scout's fetch/diff/sync pipeline.
    pi.registerProvider(PROVIDER_ID, buildProvider(loadScoutModels()));
  };

  pi.on("session_start", () => {
    reload();
    if (watching) return;
    watching = true;
    // Poll the artifact scout already writes; scout's systemd timer remains
    // the only updater of provider catalogs and LiteLLM.
    watchFile(DISPLAY_PATH, { interval: POLL_INTERVAL_MS }, reload);
  });

  pi.on("session_shutdown", () => {
    if (!watching) return;
    watching = false;
    unwatchFile(DISPLAY_PATH, reload);
  });
}
