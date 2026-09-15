# Provider Evaluation — AI model providers checked

> **Last updated:** 2026-09-14

A portable record of every AI-model provider checked for the Pi agent, with the
verdict and the reason. Free-tier only is the standing policy — no paid plans,
no credit commitments.

## Rejected

| Provider | Website URL | Date rejected | Why rejected |
|----------|-------------|---------------|--------------|
| Antigravity (Google) | N/A | 2026-08-19 | Extension unreliability (dep-vet CAUTION, 0 dependents, Scorecard 404) + free tier cut 4× (250 → 20 req/day) |
| Z.ai | https://z.ai | 2026-08-22 | Slow latency in the free tier |
| pi-antigravity-rotator | N/A | 2026-08-19 | ToS risk: multi-account rotation risks account restriction/suspension/ban |
| Cline.bot | https://cline.bot | TBD | Free models not available through Cline API |
| OpenCode Go | https://opencode.ai | TBD | $10/mo paid provider; violates free-only policy |
| AiHubMix | N/A | 2026-08-18 | Requires $1 one-time top-up for daily quotas (100 req/day, 10 req/min, 1M tokens/day shared across 51 models); 10 req/min too restrictive for agentic use; community extension immature (27 days, 1 contributor, 0 dependents) |
| NaraRouter | https://router.bynara.id/ | TBD | 15 req/min too restrictive for agentic use; free models rotate constantly with no notice and are not always available |
| Inference.net | https://inference.net/ | 2026-09-12 | Free models quality is not worth the hassle |
| ZenMux | https://zenmux.ai/ | 2026-09-12 | Free models are PAYG-only (min $5 top-up), rate limits unpublished, free models sunset to paid after ~1-week promos, classified by Anthropic as a non-official gateway/reseller |
| Merge Gateway | https://www.merge.dev/merge-gateway | 2026-09-12 | Routing proxy, not a model host; free tier has only 2 text LLMs (nemotron-3.5-lightning, minimax-h3) while the rest are media models; free list rotates with no notice (June's 4 free models were all gone by August) |
| TokenRouter | https://www.tokenrouter.com/ | 2026-09-12 | Real operator, but no stable free tier: short rotating promos (~1–4 weeks) with no published TPM/RPM/RPD, capacity throttling/429s, and high latency (Kimi K3 ~34s TTFT in one report); community-built Pi integration exists but cannot provide dependable free capacity |
| UnoRouter | https://unorouter.com/en | 2026-09-12 | Security concerns: anonymous operator (no legal entity, no SOC 2/DMARC, 3-month-old domain); 1 req/min per model too restrictive for agentic work |
| apiIndex | https://apinex.bond/ | 2026-09-12 | High-risk security concerns: anonymous operator, 6-week-old domain, empty legal pages, and model swapping detected (including paid tier) |
| Agnes AI | https://agnes-ai.com/ | 2026-09-12 | Questionable reliability: recurring latency spikes, timeouts, and insufficient evidence of dependable daily agentic use |
| Cerebras | https://cerebras.ai/ | 2026-09-12 | Free tier is limited to 5 RPM, too restrictive for agentic workflows; a verified payment method is required |
| Cloudflare Workers AI | https://developers.cloudflare.com/workers-ai/ | 2026-09-12 | The 10,000 Neurons/day free allocation is too restrictive for sustained agentic use |
| UniKey | https://www.getunikey.ai/ | 2026-09-12 | Too anonymous (no team identified, Telegram has 13 subscribers vs 180k claimed users) and too new (project ~2 months old at time of evaluation); free tier is only $0.50 in credits (5,000 credits at $1/10k), no ongoing free tier, no published rate limits |
| io.net | https://io.net | 2026-09-12 | Free tier limits not published; shared credit pool with opaque daily token budget (must sign up to discover limits); model availability uncertain |
| Venice AI | https://venice.ai | 2026-09-12 | Free tier limited to basic open-weight models only; 10 RPM too restrictive for agentic use; frontier models require Pro ($18/mo) or DIEM staking |
| Chutes AI | https://chutes.ai | 2026-09-12 | Only 2 free models (DeepSeek-R1, Llama 3.1 70B); no formal rate limits but unreliable during peak hours due to community-powered GPU capacity |
| 1min.ai | https://1min.ai/ | 2026-09-14 | $0.01/day free credits (15k/day) not worth integrating non-OpenAI-compatible API into opencode/Pi agent (requires community relay) |
| kie.ai | https://kie.ai/ | 2026-09-14 | One-time free tier (80 credits) exposed only via web playground, not via API |
| NavyAI | https://api.navy/ | 2026-09-14 | 150k tokens/day (combined input+output, per-model multipliers apply) too limited for agentic use |
| ModelScope | https://modelscope.ai/docs/model-service/API-Inference/intro | 2026-09-14 | Requires Alibaba Cloud account + real-name verification (Chinese ID or passport); no Chinese ID available |
| OVHcloud AI Endpoints | https://www.ovhcloud.com/en/public-cloud/ai-endpoints | 2026-09-14 | 2 RPM/IP on the anonymous tier too restrictive for agentic use |
| LLM7 | https://llm7.io | 2026-09-14 | 500k tokens/day (anonymous tier) too restrictive for agentic use |

## Deferred

| Provider | Website URL | Date deferred | Why deferred |
|----------|-------------|---------------|--------------|
| morphllm.com/students | https://www.morphllm.com/students | TBD | Free models for students; requires university email; worth checking later |
| Lightning AI | https://lightning.ai/ | 2026-09-12 | Free-tier evaluation requires a credit card |
| Experiential Labs | https://www.experientiallabs.ai/ | 2026-09-12 | Free-tier evaluation requires a credit card |
| AkashML | https://akashml.com | 2026-09-12 | Free-tier evaluation requires a credit card |
| xAI | https://x.ai | 2026-09-14 | $150/mo data-sharing credits (Grok 4 Fast, 2M ctx) worth checking later; blockers: $5 spend gate, irreversible team-level training opt-in, EU/UK excluded |
| GMI Cloud | https://console.gmicloud.ai/ | 2026-09-14 | Free-tier evaluation requires a credit card to claim the $5 credit; card-free access limited to 2 weak distill models only |

## Related

- [gopass.md](gopass.md) — API-key store for the live providers
- [dev/pi/docs/PI.md](../dev/pi/docs/PI.md) — Pi provider reference
- [tools/provider_registry/providers.py](../tools/provider_registry/providers.py) — runtime provider registry