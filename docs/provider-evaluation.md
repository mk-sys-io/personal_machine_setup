# Provider Evaluation — AI model providers checked

> **Last updated:** 2026-09-20

A portable record of every AI-model provider checked for the Pi agent, with the
verdict and the reason. Free-tier only is the standing policy — no paid plans,
no credit commitments.

## Summary

| Metric | Count |
|--------|-------|
| **Total providers evaluated** | 51 |
| **Rejected** | 43 |
| **Deferred** | 8 |

*Last counted: 2026-09-20*

## Active providers

Providers currently configured in `dev/opencode/opencode.jsonc`.

### Primary providers

| Provider | Description | Notes |
|----------|-------------|-------|
| Kilo | Kilo Gateway (kilo.ai) — inference routing gateway by Kilo Code Inc (acquired by Anaconda, Jul 2026); aggregates 500+ models, BYOK, zero markup. Free tier: `:free`-tagged models at $0, 200 req/hr per IP, anonymous access, no card; roster rotates | N/A |
| OpenCode Zen | Official gateway by the OpenCode team (opencode.ai/zen, base `opencode.ai/zen/v1`); curated coding models, PAYG $20 min top-up. Free tier: rotating promo models incl. big-pickle, ~200 req/day per IP (unpublished), no card; User-Agent-gated to the opencode client | N/A |

### Secondary fallback

| Provider | Notes |
|----------|-------|
| NVIDIA NIM | N/A |
| Google AI Studio | N/A |
| Atria ASI | N/A |

## Rejected

| Provider | Website URL | Date rejected | Why rejected |
|----------|-------------|---------------|--------------|
| Antigravity (Google) | N/A | 2026-08-19 | Extension unreliability (dep-vet CAUTION, 0 dependents, Scorecard 404) + free tier cut 4× (250 → 20 req/day) |
| Z.ai | https://z.ai | 2026-08-22 | Slow latency in the free tier |
| Cline.bot | https://cline.bot | TBD | Free models not available through Cline API |
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
| Mistral AI | https://mistral.ai | 2026-09-18 | 2 RPM free tier too restrictive for agentic use; frequent production reliability issues (daily outages, timeouts, speed degradation); GPT-4-class models no longer leading frontier; free tier restructured without notice (Sep 2026); data used for training by default unless manually opted out |
| AshnaAI | https://ashna.ai | 2026-09-18 | Young unproven aggregator (22-month domain, privacy-masked WHOIS); zero independent reviews anywhere; opaque free-tier limits (not published); model-routing architecture may substitute cheaper models under the hood; "free top models" claim unsubstantiated |
| apmix.ai | https://apmix.ai | 2026-09-18 | Extremely new (9-day-old domain, 3-month-old company); zero community track record; "free access to top models" claim misleading — free tier only covers 3 designated free models, not frontier models; 2M tokens one-time (not recurring); free offer already revised downward once; yearly plans non-refundable |
| Giga AI Free | https://free.gigamind.dev | 2026-09-18 | Not an OpenAI-compatible API (Claude Code proxy only); all prompts/completions stored and used for model training (24-month retention); data shared with advertising partners; no guaranteed deletion; models may switch to OSS as funding runs out; opaque dynamic rate limits; explicitly self-admitted unsustainable business model |
| TeamoRouter | https://teamorouter.com/ | 2026-09-19 | Free tier unreliable for agentic use: 200 req/day + shared 6B tokens/day DeepSeek lane / 2B GLM lane first-come-first-served, may run out early at peak with no same-day refill; no live pool counter or exhaustion history published |
| Token Factory (AMD Radeon) | https://developer.amd.com.cn/radeon/api/v1 | 2026-09-19 | Removes models without notice; latency issues and unusable during peak hours |
| Vyce AI | https://vyceai.com/ | 2026-09-19 | Untrusted proxy: ~2-month anonymous domain (NameCheap/IS privacy), ScamAdviser 0/100 + Gridinsoft 13/100 + Fortinet phishing hit, substitution flags on claude-sonnet-4-6/5 and gpt-5.6-new (only deepseek-v4-flash Matched — hallucinates tool calls), depleting promo credits ($40-50 + unstable check-in, expirable at discretion), Discord-gated, no retrievable privacy policy |
| AgentRouter | https://agentrouter.org | 2026-09-19 | Opaque Chinese 公益站 operator (no legal entity, PRC-law jurisdiction); one-time promo credits ($100–200) with daily batch limits and overnight model cuts; GitHub OAuth required; no published TPM/RPM/RPD; forwards prompts to upstream providers under PRC law |
| Bluesminds | https://api.bluesminds.com | 2026-09-19 | Anonymous operator; waitlist gating reported (Aug 2026); transitioning to per-request $5–20 pricing; free tier unreliable for agentic use |
| TaBiAI / TabiToken | https://tabitoken.com | 2026-09-19 | Claude-Opus-only catalog; one-time promo credits ($100–125); no published rate limits; GitHub age gate; inconsistent base paths across mirrors |
| KKToken | https://kktoken.cc | 2026-09-19 | Claude-Opus-only; daily check-in required; VPN needed; base path unknown; no published limits |
| JustDoWork | https://api.justwoker.icu | 2026-09-19 | GitHub ≥365d gate; daily check-in; Claude-Opus-only; no published TPM/RPM/RPD; Discord-gated support |
| GoRouter | https://gorouter.app | 2026-09-19 | Same 公益站 ecosystem; Claude-Opus-only; daily check-in; GitHub OAuth; no published limits |
| SeekAI / Xingya / XinJianYa (公益站 affiliate cluster) | seekai.cc, xingya.site, new.xinjianya.top | 2026-09-19 | Unverified operators in same affiliate ecosystem as known unreliable公益站; built on identical New-API/One-API frontend; no published free-tier limits, no independent reviews, co-listed in OmniRoute aggregator with rotating `?aff=` codes |
| Groq | https://groq.com | 2026-09-19 | Free tier 8K TPM ceiling is a per-request size limit — single request >8K tokens (input + declared max_tokens) returns HTTP 413 even with full quota; tool schemas (~2–3K tokens) + system prompt + conversation history exceed ceiling in ~3–5 agentic turns; context window (131K) unreachable on free tier; 1000 RPD sounds generous but 200K TPD binds first; community confirms Pi agent 413 failures on Groq |
| Nebius (Token Factory) | https://nebius.com | 2026-09-20 | No permanent free tier; $1 trial credit with 30-day expiry; free trial suspended Jul 2026; Builder Program too restrictive ($25/90-day, no production use); EU-only hosting adds latency for non-EU users |
| AnyAPI.ai | https://anyapi.ai/ | 2026-09-20 | Unfunded solo-founder startup (15-month domain, 1 LinkedIn employee, privacy-masked WHOIS, Hong Kong vs. NY location discrepancy); zero independent reviews anywhere; "SOC 2 Ready" not certified; ToS allows silent model substitution and hidden provider identity; ANY Token pricing obscures real USD cost; 100K ANY Tokens/day free tier too low for agentic coding; ToS §9 reserves right to kill free tier without notice |
| Baseten | https://www.baseten.co/ | 2026-09-20 | The advertised $30 one-time credit is no longer granted — silently replaced with a much smaller one-time credit ($1 Models API + $2 tool calls) with no notice; no permanent free tier; credit amount unpublished by Baseten and varies by signup path |
| Uprouter (pooled) | https://www.uprouter.online | 2026-09-20 | Unreliable; free tier ambiguous (daily compute pool via /earn, billed on success only, 200 req/day cap); friction setting up a working model on top of shady/unreliable sourced providers |

## Deferred

| Provider | Website URL | Date deferred | Why deferred |
|----------|-------------|---------------|--------------|
| morphllm.com/students | https://www.morphllm.com/students | TBD | Free models for students; requires university email; worth checking later |
| Lightning AI | https://lightning.ai/ | 2026-09-12 | Free-tier evaluation requires a credit card |
| Experiential Labs | https://www.experientiallabs.ai/ | 2026-09-12 | Free-tier evaluation requires a credit card |
| AkashML | https://akashml.com | 2026-09-12 | Free-tier evaluation requires a credit card |
| xAI | https://x.ai | 2026-09-14 | $150/mo data-sharing credits (Grok 4 Fast, 2M ctx) worth checking later; blockers: $5 spend gate, irreversible team-level training opt-in, EU/UK excluded |
| GMI Cloud | https://console.gmicloud.ai/ | 2026-09-14 | Free-tier evaluation requires a credit card to claim the $5 credit; card-free access limited to 2 weak distill models only |
| OpenRouter | https://openrouter.ai/ | 2026-09-16 | Free tier is 20 RPM / 50 req/day; a one-time $10 credit top-up permanently unlocks 1000 req/day (all-time credits, no TPM limit). Requires a $10 credit commitment, which violates the free-only policy; worth checking later |
| OrcaRouter | https://www.orcarouter.ai/ | 2026-09-19 | One-time $20 purchase permanently unlocks 20 RPM / 800 RPD (from 10/50); no TPM limit; very new (Apr 2026), GitHub history gate, rotating free lineup, minimal community track record; worth revisiting in 3–6 months |

## Related

- [gopass.md](gopass.md) — API-key store for the live providers
- [dev/pi/docs/PI.md](../dev/pi/docs/PI.md) — Pi provider reference
- [tools/provider_registry/providers.py](../tools/provider_registry/providers.py) — runtime provider registry