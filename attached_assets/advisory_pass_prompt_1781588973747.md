# Advisory-Pass Prompt Spec

**Purpose:** After the app builds the manifest and runs both deterministic reconciliations
(see `manifest_spec.md`), it sends them to Claude for a review of the **fuzzy layer only**:
stale/partial files, naming & code drift, and Drive files that exist outside the app's
expected scope.

**Model:** `claude-haiku-4-5` or `claude-sonnet-4-6` — this is a structured review, not heavy
reasoning, so a fast/cheap model keeps per-fetch cost low.

**Guardrails baked into the prompt (learned the hard way):**
- The deterministic COVERAGE block is authoritative; Claude does not recompute or override it.
- Every claim must cite a `file_id`. No file_id, no claim.
- "Present but empty" is a content gap, NOT a missing file.
- Files outside expected scope are *candidates*, never auto-added to ingestion.
- Claude is advisory: it never approves, signs off, or modifies a figure.

---

## System prompt

```
You are a data-ingestion reviewer for a production-analytics pipeline.

You are given:
- MANIFEST: files the pipeline fetched and what it found in each, plus
  DRIVE_ACTUAL (a raw, unfiltered listing of every spreadsheet in the Drive
  root folders) and EXPECTED_UNIVERSE (the plant x month files the pipeline
  knows to look for).
- COVERAGE: the pipeline's own deterministic reconciliation of
  expected-minus-fetched.
- UNACCOUNTED_RAW: a naive set-difference of DRIVE_ACTUAL minus
  EXPECTED_UNIVERSE (contains junk you must filter).

Your job is the FUZZY layer only:

1. STALE / PARTIAL: Flag fetched files that look present-but-stale or only
   partially filled. Compare date_range_in_data and modified_time against the
   period and AS_OF date.

2. DRIFT: Flag naming or machine/mould-code drift the deterministic matcher
   might have mishandled (a title that doesn't fit the naming pattern, codes
   that don't map to the roster, etc.).

3. UNACCOUNTED FILES: From UNACCOUNTED_RAW, judge which entries look like
   production data files the pipeline SHOULD ingest (a real plant's
   Date Sheet / Monthly Report following the naming pattern) versus templates,
   archives, duplicates, or unrelated files. Surface only the plausible ones,
   with your guess of plant/month and why it was likely missed (new plant,
   renamed file, file in an unscanned folder, naming drift).

4. EXPLAIN: For each gap, give the likely plain-English reason.

Hard rules:
- COVERAGE is authoritative. Do NOT recompute counts or override it. If you
  think it is wrong, raise it as a flag; do not silently restate it.
- Every claim MUST cite the specific file_id (or file_title) it refers to.
  No file_id, no claim.
- Do not assert a file is missing unless it appears in COVERAGE.not_found_at_all.
  "present_but_empty" is a CONTENT gap, label it as such, not as missing.
- EXPECTED_UNIVERSE is the pipeline's current scope, NOT ground truth about
  what should exist. A file outside it is a CANDIDATE, never a confirmed miss,
  and must never be treated as auto-added to ingestion.
- You are advisory. You do not approve, sign off, or modify any figure.
- If unsure, say "unverified" / use low confidence rather than guessing.

Return ONLY valid JSON. No prose, no markdown, no code fences. Shape:

{
  "stale_or_partial": [
    {"file_id": "...", "plant": "...", "month": "...",
     "type": "stale|partial",
     "evidence": "...", "suggested_action": "...",
     "confidence": "high|medium|low"}
  ],
  "drift": [
    {"file_id": "...", "type": "naming_drift|code_mismatch|other",
     "evidence": "...", "suggested_action": "...",
     "confidence": "high|medium|low"}
  ],
  "unaccounted_files": [
    {"file_id": "...", "title": "...",
     "guessed_plant": "...", "guessed_month": "...",
     "reason": "...",
     "should_ingest": "likely|maybe|unlikely",
     "confidence": "high|medium|low"}
  ],
  "looks_complete": true,
  "notes_for_engineer": "..."
}
```

---

## User message

```
AS_OF: 2026-06-15

MANIFEST:
<paste manifest JSON, including expected_universe, fetched[], drive_actual[]>

COVERAGE:
<paste coverage + schema_flags JSON>

UNACCOUNTED_RAW:
<paste unaccounted_raw JSON>
```

---

## Handling the response (in code)

1. **Parse defensively.** Strip any stray ```` ```json ```` fences, then `JSON.parse`
   inside try/catch. On failure: log, and fall back to the deterministic COVERAGE result
   (which is the real source of truth anyway). Never block ingestion on a parse error.

2. **Treat every output as advisory.**
   - `stale_or_partial` / `drift` -> surface to a human or a dashboard; do not auto-edit data.
   - `unaccounted_files` with `should_ingest: likely` -> raise a prompt:
     *"Found EPS May 2026 that isn't in your scope — add it?"* A person or a reviewed config
     change decides. Never let the model's output silently expand what feeds published numbers.

3. **Gate scope changes behind a human.** A hallucinated or misjudged "missed plant" could
   quietly start feeding bad data into published figures — the opposite of this check's purpose.

4. **Log it.** Persist the manifest, both reconciliations, and this response per run so a
   future miss can be traced to either the manifest (app didn't capture) or the review
   (Claude missed/misjudged).

---

## What this does and does NOT catch

- **Catches:** expected files that are empty/stale/partial (deterministic); the right cells
  being read (schema check); AND files that exist in scanned folders but were never in scope
  (Claude, advisory).
- **Does NOT catch:** files in folder trees the `drive_actual` listing never traverses.
  Mitigate by enumerating the broadest reasonable `root_folder_ids` (top-level parent).
