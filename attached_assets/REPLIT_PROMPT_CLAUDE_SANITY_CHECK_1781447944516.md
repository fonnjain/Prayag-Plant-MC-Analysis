# Claude sanity-check on Drive-fetched data (assessment only, not in the data path)

After Replit's Google Drive connector fetches the daily files and runs its own **deterministic**
pre-checks, send a compact summary to Claude for a plausibility/anomaly review. Claude returns a
structured verdict and issue list; Replit decides proceed/hold. Companion to
`REPLIT_PROMPT_INGESTION_AND_DASHBOARD.md` (§4 pipeline) and `prayag_mis_schema.sql` (`data_review`).

## Where it sits
`Drive fetch → Replit deterministic pre-checks → **Claude sanity-check (this step)** → calculations
→ dedup/load → dashboards`. This is distinct from `REPLIT_PROMPT_DATA_VERIFICATION.md`: that is a
purely deterministic control-total reconciliation *after* load; this is a reasoning-layer plausibility
review *before* load. They are complementary.

## Guardrails (must hold)
- Claude **assesses**; it never computes, recomputes, or corrects a figure. The numbers are produced
  deterministically by Replit and are identical regardless of what Claude says.
- Claude judges only from the stats/baselines/samples it is given. It must never invent data.
- Uses the **FAST tier** model (the §4b data-review call in `REPLIT_PROMPT_MODEL_TIERING.md`).
- **Never send the full dataset** — only per-feed stats, the deterministic-check results, prior-day
  baselines, and a few sample rows. Minimise PII in samples.
- Graceful fallback: if the model call fails, log it, skip the AI review, and proceed on Replit's
  deterministic checks alone (don't fail the pipeline because the model was unreachable). A `hold`
  from Claude, however, does stop the run.

## Input Replit sends to Claude (compact JSON, per run)
```json
{
  "as_of_date": "2026-06-14",
  "run_id": 412,
  "feeds": [
    {
      "key": "payment_main", "department": "Finance & Accounts",
      "file_id": "1n4GUR8...", "modified_time": "2026-06-14T15:40:00+05:30", "stale": false,
      "row_count": 142,
      "deterministic_checks": {
        "missing_cols": [], "null_business_keys": 0, "negative_values": 0,
        "dupe_business_keys": 0, "header_vs_lines_delta": 0.0, "unparsed_dates": 0
      },
      "stats": {"sum_amount": 943500, "min_date": "2026-06-14", "max_date": "2026-06-14",
                "distinct_parties": 11},
      "baseline": {"avg_row_count_7d": 130, "avg_sum_amount_7d": 1100000,
                   "last_run_sum_amount": 1080000},
      "samples": [ { "...3-5 representative rows..." } ]
    }
  ]
}
```

## Output Claude must return (strict JSON, nothing outside it)
```json
{
  "verdict": "pass | pass_with_warnings | hold",
  "issues": [
    {"feed": "payment_main", "severity": "info | warn | error",
     "type": "anomaly | staleness | coverage | outlier | consistency | reconciliation",
     "message": "Collections ~1/10th of the 7-day average on a working day — possible partial sheet",
     "evidence": "sum_amount 94,350 vs avg 11,00,000",
     "suggested_action": "proceed | review | hold_feed"}
  ],
  "cross_feed_notes": ["Sales billed today but dispatch row_count = 0 — verify dispatch sheet"],
  "summary": "one-line overall assessment"
}
```

## What Claude should look for (judgment the deterministic checks can't make)
- **Coverage / plausibility vs history** — a feed far below/above its 7-day baseline; row_count near
  zero on a working day; a feed whose max_date isn't today (stale despite being "modified").
- **Outliers / unit errors** — a value ~10× typical (e.g., a payment entered in paise, a qty in the
  wrong unit).
- **Internal consistency** — header total vs sum-of-lines already flagged; receivables buckets not
  summing; production rejection implausibly high/zero.
- **Cross-feed sanity** — sales booked but zero dispatch; production with no matching orders; large
  collections with no change in receivables.
- **Duplicate-looking entities** — same party/item under spelling variants in the samples.
- It does **not** re-add columns or restate totals; it points at the number that looks wrong.

When to choose each verdict: `hold` only for likely-corrupt or materially-wrong data (empty/stale on
a working day, ~10× outliers, header-vs-lines mismatch). Normal day-to-day variation is `pass`.
Minor, non-blocking concerns are `pass_with_warnings`.

## Embeddable prompt (pass to the Anthropic API, FAST tier)
**System:**
```
You are a data sanity-check reviewer for a manufacturing MIS pipeline. You receive a JSON summary of
daily files fetched from Google Drive: per-feed row counts, deterministic check results, summary
statistics, 7-day baselines, and a few sample rows. Assess whether the data looks plausible and safe
to load.

Rules:
- You assess only. Never compute, recompute, correct, or output any figure as if authoritative.
- Judge only from the provided stats, baselines, and samples. Never invent data or assume values you
  were not given.
- Flag coverage gaps, outliers/unit errors, internal inconsistencies, cross-feed contradictions, and
  staleness. Reference the specific feed and the number that triggered each issue.
- Choose "hold" only for likely-corrupt or materially-wrong data; treat normal variation as "pass".
- Respond with ONLY the JSON object in the schema given. No prose before or after it.
```
**User (template Replit fills per run):**
```
Review this run and return the verdict JSON.

Schema:
{verdict, issues:[{feed,severity,type,message,evidence,suggested_action}], cross_feed_notes:[], summary}

Data:
{INSERT the compact per-run JSON payload here}
```

## How Replit consumes the verdict
- `pass` → continue to calculations/load.
- `pass_with_warnings` → continue, but surface warnings in the Data Confirmation screen / run log.
- `hold` → stop before load, alert a human, do not write facts for the held feed(s).
- Persist the result to `data_review` (`model`, `model_version`, `tier`, `verdict`, `issues`,
  `downgraded`) and link to the report footer (e.g. "Sanity-check: claude-sonnet-4-6 · fast tier · pass").

## Acceptance criteria
- The sanity-check runs after deterministic pre-checks and before any load; its verdict + model/tier
  are stored on `data_review`.
- Claude returns valid JSON only; a parse failure is treated as `pass_with_warnings` + logged (never
  silently dropped).
- A `hold` verdict stops the load for the affected feed(s) and raises a human alert.
- If the model is unreachable, the run proceeds on deterministic checks and the skip is logged.
- No figure in the stored facts ever originates from or is altered by this step (verifiable from logs).
