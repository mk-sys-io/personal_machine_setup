# Detection is declarative descriptors, not code

- Status: Accepted (2026-10-31) · Source: scout/providers/README.md, scout/docs/operating.md, docs/adr/016-litellm-substrate-scout-awareness.md

## Decision

Whether a model counts as free is declared per provider in a YAML descriptor
(suffix rules, `pricing_zero`, flag keys, virtual-router shape), not implemented as
per-provider code. Adding a provider means adding a file; no scout source change.
The `scout add --blacklist` CLI is retired: the file edit IS the interface (D16).

## Why

- Triggering constraint: the cheahjs lesson — grammar and worked examples must version
  together; per-provider code rots independently of its docs.
- Rejected: imperative `scout add` CLI — duplicates the filesystem control plane and
  drifts from the watched files. Rejected: code-native per-provider matchers — every
  new provider needs a release; descriptors need none. Rejected: last-wins name merge
  — silent collisions; cross-file duplicate `name:` is now a hard error.
- Revisit only if: the matcher grammar proves insufficient for a real provider
  (grammar is locked 2026-10-31 and versioned with the descriptors).

## Consequences

Good: providers onboard by file; unknown `type:`/top-level keys and cross-file name
collisions fail `scout validate` before any fetch. Bad (accepted cost): the grammar
must be learned to author a descriptor; a wrong rule silently keeps a paid model
(mitigated by `pricing_zero` strictness and the D14 verified/assumed taxonomy).