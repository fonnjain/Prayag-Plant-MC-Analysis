# Wire tiered model selection (fast model for daily/weekly, deep model for monthly/quarterly)

Pick the Anthropic model by report cadence: a fast, cost-effective model for frequent daily/weekly runs, and a heavier model for the less-frequent monthly / quarterly / board reviews where deeper reasoning is worth the cost. **This applies only to the language calls (the report narrative and the data-review assessment). It must not touch the data path** — reading, parsing, arithmetic and validation stay deterministic, and the numbers are identical no matter which model writes the prose.

## 1. Config (Secrets, with safe defaults)
Read model names from Secrets so they can change without code edits:
- `ANTHROPIC_MODEL_FAST`  → default `claude-sonnet-4-6`
- `ANTHROPIC_MODEL_DEEP`  → default `claude-opus-4-8`
- optional token budgets: `MAX_TOKENS_FAST` (e.g. 1500), `MAX_TOKENS_DEEP` (e.g. 4000)

## 2. Tier map (period / report kind → tier)
- **FAST tier** — `yesterday`, `daily`, `last_7_days`, `weekly`.
- **DEEP tier** — `monthly`, `quarterly`, `fiscal_year`, and any report flagged `board` / `executive_review`.
- A manual override always wins: an explicit `deep_analysis=true` (or a "Deep analysis" toggle in the UI / a `--deep` flag) forces the DEEP tier for that run; `deep_analysis=false` forces FAST.

## 3. One selection helper, used by every Anthropic call
Centralise it so there is a single source of truth:

```python
import os

FAST = os.environ.get("ANTHROPIC_MODEL_FAST", "claude-sonnet-4-6")
DEEP = os.environ.get("ANTHROPIC_MODEL_DEEP", "claude-opus-4-8")
MAX_FAST = int(os.environ.get("MAX_TOKENS_FAST", "1500"))
MAX_DEEP = int(os.environ.get("MAX_TOKENS_DEEP", "4000"))

DEEP_PERIODS = {"monthly", "quarterly", "fiscal_year"}

def select_model(period_type: str, *, override: bool | None = None, board: bool = False):
    """Return (model, max_tokens). override=True forces deep, False forces fast."""
    if override is True:
        tier = "deep"
    elif override is False:
        tier = "fast"
    elif board or (period_type or "").lower() in DEEP_PERIODS:
        tier = "deep"
    else:
        tier = "fast"
    return (DEEP, MAX_DEEP) if tier == "deep" else (FAST, MAX_FAST)
```

Use it at **both** call sites — the narrative writer and the §4b data-review assessment — passing the current period/report context:

```python
model, max_tokens = select_model(period_type, override=deep_flag, board=is_board_report)
resp = client.messages.create(model=model, max_tokens=max_tokens, messages=[...])
```

## 4. Record which model was used (provenance)
Store the chosen model name on the artefacts that already carry metadata: the report record and the `data_review` row (`model` / `model_version`, plus `tier`). Show it in the Data Confirmation / report footer (e.g. "Analysis: claude-opus-4-8 · deep tier"). This keeps the audit trail complete and makes the tiering visible.

## 5. Graceful fallback
If a `messages.create` call fails because the deep model is unavailable or returns an error, **retry once with the FAST model and log that a downgrade happened** (record it on the artefact too). Never fail a report just because the deep model was unreachable.

## 6. Guardrails (must hold)
- Tiering changes **only** the narrative and the data-review text. It never changes which data is read, the metrics, the validation, or the stored facts. The same period produces the same numbers on either tier.
- The deep tier is for depth of reasoning/explanation, not for "getting different figures".
- Keep all model calls behind the one helper so there is no place a model is hard-coded.

## 7. Acceptance criteria
- A daily or weekly run uses `ANTHROPIC_MODEL_FAST`; a monthly/quarterly/board run uses `ANTHROPIC_MODEL_DEEP` — verifiable from the recorded `model`/`tier` on the report and review row.
- The "Deep analysis" override forces the deep model on an otherwise-fast period, and vice-versa.
- Changing the Secrets swaps models with no code change.
- Identical input data yields identical numeric tables on both tiers (only the prose differs).
- If the deep model errors, the run completes on the fast model and the downgrade is logged.
