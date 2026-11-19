// Typecheck-only ambient declarations for Pi extensions.
// Runtime modules come from the Pi process; these declarations exist only so
// `tsc -p dev/pi` passes without installing Pi's packages into the repo.
// This file is NOT deployed.

declare module "@earendil-works/pi-coding-agent" {
  export interface ModelCost {
    readonly input: number;
    readonly output: number;
    readonly cacheRead: number;
    readonly cacheWrite: number;
  }

  export interface ModelDefinition {
    readonly id: string;
    readonly name: string;
    readonly reasoning: boolean;
    readonly input: readonly string[];
    readonly cost: ModelCost;
    readonly contextWindow: number;
    readonly maxTokens: number;
  }

  export interface ProviderConfig {
    readonly name: string;
    readonly baseUrl: string;
    readonly apiKey: string;
    readonly api: string;
    readonly models: readonly ModelDefinition[];
    readonly refreshModels: () => Promise<ModelDefinition[]>;
  }

  export interface ExtensionAPI {
    on(
      event: "session_start" | "session_shutdown",
      handler: (event: unknown) => void | Promise<void>,
    ): void;
    registerProvider(name: string, config: ProviderConfig): void;
  }
}

declare module "node:fs" {
  export function existsSync(path: string): boolean;
  export function readFileSync(path: string, encoding: "utf8"): string;
  export function watchFile(
    path: string,
    options: { readonly interval: number },
    listener: () => void,
  ): void;
  export function unwatchFile(path: string, listener?: () => void): void;
}

declare module "node:os" {
  export function homedir(): string;
}

declare module "node:path" {
  export function join(...parts: string[]): string;
}
