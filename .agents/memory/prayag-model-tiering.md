---
name: Prayag tiered model selection & cache keying
description: Why every per-data Claude cache in the Prayag app must include the resolved model in its key, and how provenance must reflect the model actually used.
---

# Tiered model selection cache trap

The Prayag app picks a fast vs deep Anthropic model by period tier (daily/weekly = fast; monthly/quarterly/FY = deep), with an optional forced-deep override.

## Rule
Any cache keyed to a data state (fingerprint, period key, metrics summary) MUST also include the **resolved model** in its key. Otherwise a fast-tier response gets reused when deep is later forced for the same data — the override silently does nothing.

**Why:** there are TWO independent caches that both had this bug — the in-module narrative/sanity cache, and a separate app-level `_claude_reviews` review cache. Fixing only one leaves the bypass open through the other. A reviewer caught the second after the first looked complete.

**How to apply:** resolve `select_model(period_type, override)` BEFORE the cache lookup and fold the model into the key. If you add any new Claude-text cache, do the same. There is a regression test asserting same-fingerprint + different tier produces different keys.

## Provenance must be honest
Provenance shown to the user (e.g. PDF footer) must reflect the model that **actually** produced the text, not the intended tier — a deep request that fell back to fast must say fast. The text-creation helper returns `(text, actual_model)` for this; `model_label()` = intended tier (templates), `tier_label(actual_model)` = honest after fallback.
