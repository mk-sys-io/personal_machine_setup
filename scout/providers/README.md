# Providers — descriptors and matcher grammar

One YAML file per provider in this directory. A dropped/edited file is the
entire control plane (D16); `scout validate` checks the contract, `scout diff`
previews the effect.

## Descriptor schema

Required keys: `name`, `base_url`, `auth`, `free_detection.rules`.
Optional keys shown with defaults.

```yaml
name: kilo                         # required str; identity key (dup across files rejects the run)
base_url: https://ai-gateway.kilo.ai/api/v1   # required str
auth: none                          # required: none | env/NAME  (scout never holds raw keys)
catalog:                            # optional
  endpoint: /models                 # default /models
  models_path: data                  # default data
free_detection:                     # required
  rules:                            # non-empty list of rule maps; any pass => free (D5)
    - type: suffix
      value: [":free", "-free"]     # Kilo uses both spellings
    - type: pricing_zero            # string/number + flat/nested/buried price shapes
    - type: flag                    # explicit boolean on the record
      key: isFree                   # default isFree
    - type: virtual_router          # zero-priced with null/empty provider metadata
capabilities:                       # optional
  include_keywords: [chat, mini, preview]
  exclude_keywords: [embedding, rerank, whisper, tts, image, imagine, moderation]
quotas:                             # optional
  rpm: 60
assume_free: false                  # optional bool (D14): plan-based, not metadata-reflectable
```

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
| `virtual_router` | zero-priced record with null/empty provider metadata | none |

`value` may be given as a single string (normalized to a one-element list).

## Confidence taxonomy (D14)

- **verified free** — metadata/suffix evidence in the catalog response (metadata only;
  not completion-tested, D10 revoked).
- **assumed free** — provider's descriptor sets `assume_free: true` because free-ness is
  plan-based (e.g. Groq, Google AI Studio, Cloudflare Workers AI).

## Admission checklist (D13)

A provider is onboarded only if it exposes a machine-queryable model list whose
free-ness is reflectable reliably and regularly via API. Providers whose only free
signal is trial credits (NVIDIA NIM, Fireworks, Novita) or subscription
entitlements (GitHub Copilot) fail admission — an assessment to decline, not a
feature to build workarounds for. No manual `include:` fallback exists in v1.