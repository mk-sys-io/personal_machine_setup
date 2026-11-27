# Providers — descriptors and matcher grammar

One YAML file per provider in this directory. A dropped/edited file is the
entire control plane; `scout validate` checks the contract, `scout diff`
previews the effect.

## Descriptor schema

Required keys: `name`, `base_url`, `auth`, `catalog`, `free_detection.rules`.
Optional keys shown with defaults.

```yaml
name: kilo                         # required str; identity key (dup across files rejects the run)
base_url: https://ai-gateway.kilo.ai/api/v1   # required str
auth: none                          # required: none | env/NAME  (scout never holds raw keys)
catalog:                            # required
  endpoint: /models                 # default /models
  models_path: data                  # default data
  context_window_paths:             # required: non-empty list of field paths
    - context_length
    - top_provider.context_length
  max_tokens_paths:                 # required: non-empty list of field paths
    - max_completion_tokens
    - top_provider.max_completion_tokens
free_detection:                     # required
  rules:                            # non-empty list of rule maps; any pass => free
    - type: suffix
      value: [":free", "-free"]     # Kilo uses both spellings
    - type: pricing_zero            # string/number + flat/nested/buried price shapes
    - type: flag                    # explicit boolean on the record
      key: isFree                   # default isFree
capabilities:                       # optional
  include_keywords: [chat, mini, preview]
  exclude_keywords: [embedding, rerank, whisper, tts, image, imagine, moderation]
quotas:                             # optional
  rpm: 60
assume_free: false                  # optional bool: plan-based, not metadata-reflectable
```

Adding a provider = dropping one YAML file and touching nothing else. Identity keys
on `name:`, not filename, so file renames and template copies are safe; a duplicate
`name:` across two files is a hard error that aborts the run before any fetch.

Unknown top-level keys, unknown rule `type:` values, missing required keys, and
wrong types are contract errors (`scout validate`, `file:line:col`, exit 1).

## Matcher grammar (structured form, locked 2026-10-31)

Rule syntax is a structured map `- type: <name>` with optional matcher keys
(`value`, `key`). No expression mini-language, no parser. Unknown `type:` errors
loudly at load/validate time; never silently skipped.

| Type | Meaning | Keys |
|---|---|---|
| `suffix` | model id ends with one of `value` | `value: list[str]` (required) |
| `pricing_zero` | every price field in the record is zero/absent | none (checks all price shapes) |
| `flag` | metadata boolean is true | `key: str` (default `isFree`) |

`value` may be given as a single string (normalized to a one-element list).

## Capabilities filter

`include_keywords` keeps (chat, mini, preview, flagship, …); `exclude_keywords` drops
(embedding, rerank, whisper, tts, image, imagine, moderation, …). Resolution order
per model: global blacklist → per-provider blacklist → free detection → capability
filter (cheapest checks first).

When keyword matching is ambiguous, fall back to provider-catalog modality fields:
`architecture.output_modalities` (Kilo) or `architecture.modality` (OpenRouter). A
record whose output modality is image/audio-only drops; text-capable records keep
(including vision-capable chat models such as Nemotron VL — they output text; only
non-text-output models drop). Raw records carry no `mode:` field; that field is
LiteLLM-side only.

## Confidence taxonomy

- **verified free** — metadata/suffix evidence in the catalog response (metadata only;
  not completion-tested; completion-testing was considered and rejected).
- **assumed free** — provider's descriptor sets `assume_free: true` because free-ness is
  plan-based (e.g. Groq, Google AI Studio, Cloudflare Workers AI).
- **rule badges** — every admitted model carries the rule type that admitted it
  (suffix, pricing_zero, flag) or assumed (descriptor-level assume_free),
  in snapshot verdicts, report add-lines, and catalog.display.json;
  badges are metadata only and never enter registered model names.

## Admission checklist

A provider is onboarded only if it exposes a machine-queryable model list whose
free-ness is reflectable reliably and regularly via API. Providers whose only free
signal is trial credits (NVIDIA NIM, Fireworks, Novita) or subscription
entitlements (GitHub Copilot) fail admission — an assessment to decline, not a
feature to build workarounds for. No manual `include:` fallback exists in v1.

YAGNI promotion: a new detection pattern is promoted to a matcher only when an
onboarding-worthy provider demands it (NaraRouter's plan-allowlist mechanism was
the candidate and was deliberately not built — its `/api/plans` endpoint now 404s).
Adding a matcher later = one evaluator branch in scout.py plus one descriptor line;
that is the predicate-chain contract, not hardcoding detection per provider.

## Detection patterns considered (v1 implements 3)

v1 matchers: suffix (`:free`/`-free`), pricing_zero (all price shapes), flag
(`isFree`). Not implemented: plan/quota-based → `assume_free: true`; trial credits / subscriptions
→ fail admission; dated promos / keyless access → on demand. Refuted: `@free`/
`free/` prefixes, `/v1/free` endpoints, `free_only=` params, rate-limit headers
as a free/paid discriminator, `expiration_date` on free models.
