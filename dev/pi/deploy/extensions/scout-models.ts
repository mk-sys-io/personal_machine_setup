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
    entries.push({ id, badge: typeof badge === "string" ? badge : "" });
  }
  return entries;
}

function toModel(entry: ScoutEntry): ModelDefinition {
  const slash = entry.id.indexOf("/");
  const provider = slash === -1 ? "" : entry.id.slice(0, slash);
  const modelId = slash === -1 ? entry.id : entry.id.slice(slash + 1);
  const providerLabel = provider.length > 0 ? ` [${provider}]` : "";
  const badgeLabel = entry.badge.length > 0 ? ` [${entry.badge}]` : "";

  return {
    id: entry.id,
    name: `${modelId}${providerLabel}${badgeLabel}`,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 32_768,
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
