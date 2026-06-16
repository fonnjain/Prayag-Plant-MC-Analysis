"""
Optional Claude narrative — used ONLY to write prose from already-computed numbers.
Claude never reads, transcribes, or recomputes any figure.
Requires ANTHROPIC_API_KEY in environment. Cache per (period, view).
"""
from __future__ import annotations
import os
import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger("prayag.narrative")

_cache: dict[str, str] = {}
# The model that ACTUALLY produced each cached entry (records deep→fast
# fallback so provenance shown to the user stays honest), keyed by cache key.
_actual_model: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Tiered model selection — a fast, cheap model for frequent daily/weekly runs
# and a heavier model for the less-frequent monthly/quarterly/board reviews.
# This affects ONLY the language calls (prose + data-review text). It never
# touches the data path: the numbers are identical no matter which model writes.
# Model names come from Secrets so they can change without code edits.
# ---------------------------------------------------------------------------
FAST_MODEL = os.environ.get("ANTHROPIC_MODEL_FAST", "claude-sonnet-4-6")
DEEP_MODEL = os.environ.get("ANTHROPIC_MODEL_DEEP", "claude-opus-4-8")
MAX_TOKENS_FAST = int(os.environ.get("MAX_TOKENS_FAST", "1500"))
MAX_TOKENS_DEEP = int(os.environ.get("MAX_TOKENS_DEEP", "4000"))

# Period kinds that warrant the deep tier (the infrequent, high-stakes reviews).
DEEP_PERIODS = {"monthly", "quarterly", "fiscal_year"}


def select_model(
    period_type: Optional[str] = None,
    *,
    override: Optional[bool] = None,
    board: bool = False,
) -> tuple[str, int, str]:
    """Return ``(model, max_tokens, tier)`` for a language call.

    ``override=True`` forces the deep tier, ``override=False`` forces fast; a
    manual override always wins. Otherwise a board/executive review or a deep
    period kind selects the deep tier; everything else uses fast.
    """
    if override is True:
        tier = "deep"
    elif override is False:
        tier = "fast"
    elif board or (period_type or "").lower() in DEEP_PERIODS:
        tier = "deep"
    else:
        tier = "fast"
    if tier == "deep":
        return DEEP_MODEL, MAX_TOKENS_DEEP, "deep"
    return FAST_MODEL, MAX_TOKENS_FAST, "fast"


def model_label(
    period_type: Optional[str] = None,
    *,
    override: Optional[bool] = None,
    board: bool = False,
) -> str:
    """Human-readable provenance string, e.g. ``"claude-opus-4-8 · deep tier"``."""
    model, _, tier = select_model(period_type, override=override, board=board)
    return f"{model} · {tier} tier"


def tier_label(model: str) -> str:
    """Provenance string for the model that ACTUALLY wrote the prose."""
    tier = "deep" if model == DEEP_MODEL else "fast"
    return f"{model} · {tier} tier"


def _create_text(model: str, max_tokens: int, prompt: str) -> tuple[str, str]:
    """Run one Anthropic text call, retrying once on the FAST model if a deep
    model is unavailable/errors. Returns ``(text, model_actually_used)`` so the
    caller can record honest provenance even after a fallback.

    The downgrade is logged so the audit trail records that it happened. A
    fast-tier call that fails is not retried (there is nothing cheaper to fall
    back to) and the exception propagates to the caller's graceful handler.
    """
    import anthropic
    # Bound the call hard: the narrative is optional (callers degrade to None on
    # error), so it must never hang a web worker. Without this the SDK default
    # timeout is ~10 minutes, which blows past the gunicorn worker timeout and
    # turns a slow Claude response into a 500 for the whole page.
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=20.0,
        max_retries=1,
    )
    try:
        msg = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip(), model
    except Exception as e:
        if model != FAST_MODEL:
            logger.warning(
                "Deep model %s failed (%s); retrying once on fast model %s.",
                model, e, FAST_MODEL,
            )
            msg = client.messages.create(
                model=FAST_MODEL, max_tokens=min(max_tokens, MAX_TOKENS_FAST),
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip(), FAST_MODEL
        raise


def _enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _cache_key(view: str, period_key: str, metrics_summary: dict, model: str) -> str:
    # The model is part of the key so a fast-tier response is never reused for a
    # request that resolves (or is forced) to the deep tier, and vice versa.
    payload = json.dumps(
        {"view": view, "period": period_key, "metrics": metrics_summary, "model": model},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def get_narrative(
    view: str,
    period_label: str,
    period_key: str,
    metrics_summary: dict,
    extra_context: str = "",
    period_type: Optional[str] = None,
    deep: Optional[bool] = None,
    provenance: Optional[dict] = None,
) -> Optional[str]:
    """
    Return a Claude-written narrative paragraph from pre-computed metrics.
    Returns None if ANTHROPIC_API_KEY is not set or on any error.
    Claude receives only the already-computed numbers, never raw sheet data.
    The model tier follows ``period_type`` (fast for daily/weekly, deep for
    monthly/quarterly/FY); ``deep=True`` forces the deep tier. When a
    ``provenance`` dict is passed it is filled with ``{"model", "label"}`` for
    the model that ACTUALLY produced the text (honest after any fallback).
    """
    if not _enabled():
        return None

    model, max_tokens, _ = select_model(period_type, override=deep)
    ck = _cache_key(view, period_key, metrics_summary, model)
    if ck in _cache:
        used = _actual_model.get(ck, model)
        if provenance is not None:
            provenance["model"] = used
            provenance["label"] = tier_label(used)
        return _cache[ck]

    try:
        metrics_text = "\n".join(f"  {k}: {v}" for k, v in metrics_summary.items())
        prompt = f"""You are writing a concise management narrative for a plastics manufacturing analytics dashboard.
The view is: {view}
Reporting period: {period_label}
{extra_context}

Pre-computed performance metrics (these are final; do not recalculate):
{metrics_text}

Write 2-3 sentences of plain-English management commentary on these results.
Focus on key insights: what is performing well, what needs attention, and any notable trends.
Be specific and factual — reference the actual numbers provided.
Do not use markdown formatting. Write in a professional, concise tone suitable for a factory manager."""

        text, used = _create_text(model, min(max_tokens, 600), prompt)
        _cache[ck] = text
        _actual_model[ck] = used
        if provenance is not None:
            provenance["model"] = used
            provenance["label"] = tier_label(used)
        return text
    except Exception:
        return None


def match_codes(unmatched: list[str], master_codes: list[str]) -> dict:
    """Fuzzy-map data machine codes to master machine codes using Claude.

    Maps NAMES only (e.g. recognise data "M/C-7" as master "EXT-02"); no figure
    is read or computed. Returns {data_code: master_code} for confident matches
    only, or {} when the key is missing / on any error. Cached per code-set.
    """
    if not _enabled() or not unmatched or not master_codes:
        return {}

    ck = "match:" + hashlib.sha256(
        json.dumps({"u": sorted(unmatched), "m": sorted(master_codes)},
                   sort_keys=True).encode()
    ).hexdigest()
    if ck in _cache:
        try:
            return json.loads(_cache[ck])
        except (ValueError, TypeError):
            return {}

    try:
        prompt = (
            "You match factory machine codes between two lists. These are CODES "
            "(names), not numbers — never invent a match.\n"
            f"Data codes: {json.dumps(unmatched)}\n"
            f"Master codes: {json.dumps(master_codes)}\n"
            "Return ONLY a JSON object mapping each data code that confidently "
            "refers to the same physical machine as a master code, to that master "
            "code. Omit any data code with no confident match. No prose."
        )
        # Utility classification — always the fast tier; never period-driven.
        text, _ = _create_text(FAST_MODEL, 400, prompt)
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):text.rfind("}") + 1]
        mapping = json.loads(text)
        clean = {
            str(k): str(v)
            for k, v in mapping.items()
            if str(k) in unmatched and str(v) in master_codes
        }
        _cache[ck] = json.dumps(clean)
        return clean
    except Exception:
        return {}


def summarize_confirmation(
    status: str,
    score_label: str,
    issues_brief: list[str],
) -> Optional[str]:
    """Plain-English summary of an ALREADY-COMPUTED confirmation result.

    Receives only the status, the completeness score string, and short issue
    descriptions — never raw sheet data or figures to compute. Returns None when
    the key is missing or on any error.
    """
    if not _enabled():
        return None

    ck = "confsum:" + hashlib.sha256(
        json.dumps({"s": status, "sc": score_label, "i": issues_brief},
                   sort_keys=True).encode()
    ).hexdigest()
    if ck in _cache:
        return _cache[ck]

    try:
        issues_text = "\n".join(f"  - {s}" for s in issues_brief[:25]) or "  (none)"
        prompt = f"""You are summarising a data-confirmation report for a factory production dashboard.
Overall status: {status}
Completeness score: {score_label}
Issues found (already detected; do not recalculate or judge the numbers):
{issues_text}

Write 2-3 plain-English sentences for a factory manager explaining whether the data
can be trusted, what is missing or flagged, and what to check. Do not invent figures.
Do not use markdown. Be concise and factual."""
        # Brief report summary — always the fast tier.
        text, _ = _create_text(FAST_MODEL, 260, prompt)
        _cache[ck] = text
        return text
    except Exception:
        return None


def claude_sanity_check(
    confirmation: dict,
    computed_metrics: dict,
    period_label: str,
    period_type: Optional[str] = None,
    deep: Optional[bool] = None,
) -> Optional[str]:
    """Deep sanity check of an ALREADY-COMPUTED confirmation result.

    Claude receives the tier issues (detected by deterministic Python), the
    completeness scores, and pre-computed metrics. It explains each issue in
    plain English, suggests corrections, and gives a readiness verdict.

    Claude is NOT passed raw sheet data and does NOT compute any figure.
    Result is cached by fingerprint — a re-check of unchanged data is instant.
    """
    if not _enabled():
        return None

    model, max_tokens, _ = select_model(period_type, override=deep)
    fingerprint = confirmation.get("fingerprint", "")
    # Model is part of the key so a fast-tier review is never reused for a
    # deep-tier (or forced) request of the same data state, and vice versa.
    ck = "sanity:" + fingerprint + ":" + model
    if ck in _cache:
        return _cache[ck]

    try:
        issues = confirmation.get("issues", [])
        if issues:
            lines = []
            for i in issues:
                loc_parts = []
                if i.get("plant"):   loc_parts.append(i["plant"])
                if i.get("machine"): loc_parts.append(i["machine"])
                if i.get("month"):   loc_parts.append(i["month"])
                loc = " > ".join(loc_parts) if loc_parts else "Overall"
                lines.append(
                    f"  [{i.get('tier_label','?')} / {i.get('severity','?')}] "
                    f"{loc}: {i.get('message','')}"
                )
            issues_text = "\n".join(lines)
        else:
            issues_text = "  (none — all four tiers passed)"

        score = confirmation.get("score", {})
        def _pair(key): p = score.get(key, [0, 0]); return f"{p[0]}/{p[1]}"
        completeness = (
            f"Files {_pair('files')}, Machines {_pair('machines')}, "
            f"Months {_pair('months')}"
        )

        metrics_text = "\n".join(f"  {k}: {v}" for k, v in computed_metrics.items())

        prompt = f"""You are reviewing production data quality for a plastics manufacturing plant before a manager signs off on the figures.

Period: {period_label}
Overall status: {confirmation.get('status','?')} — {confirmation.get('score_label','')}
Completeness: {completeness}

Pre-computed performance metrics (do not recalculate):
{metrics_text}

Issues already detected by deterministic four-tier validation (do not re-detect or add new ones):
{issues_text}

Write a structured review with exactly these four sections, separated by blank lines:

DATA HEALTH: One sentence summarising the overall state.

ISSUE ANALYSIS: For each issue listed above, explain in plain English what it likely means physically (e.g. "hours entered in minutes instead of hours", "machine was idle or under repair", "unit mismatch between kg and tonnes"). Name the specific plant, machine, and month. If multiple issues share the same root cause, group them.

CORRECTIONS NEEDED: A numbered list of the specific changes to make in the source Google Sheet, with plant name, machine code, month, and what value to fix. Only list corrections for errors — warnings can be noted as advisory.

READINESS VERDICT: State clearly either "Ready to sign off" (if only advisory warnings remain after corrections) or "Must fix X before sign-off" listing what X is.

Rules: Do not use markdown formatting (no bold, no asterisks, no hyphens as bullets). Do not invent figures. Do not recalculate anything. Write in plain English for a factory manager. Maximum 400 words."""

        text, _ = _create_text(model, max(max_tokens, 700), prompt)
        _cache[ck] = text
        return text
    except Exception:
        return None


def advisory_review(
    manifest_summary: dict,
    coverage: dict,
    as_of: str,
) -> Optional[dict]:
    """Advisory-only Claude review of the fuzzy ingestion layer.

    Sends a compact manifest summary + deterministic coverage to the fast model
    and parses its JSON response. Returns the parsed dict on success, None on
    any failure. Every output is advisory — callers must never auto-modify a
    figure or expand ingestion scope based on this response.
    """
    if not _enabled():
        return None

    system = (
        "You are a data-ingestion reviewer for a production-analytics pipeline.\n\n"
        "You are given:\n"
        "- MANIFEST: files the pipeline fetched and what it found in each.\n"
        "- COVERAGE: the pipeline's own deterministic reconciliation of "
        "expected-minus-fetched.\n"
        "- UNACCOUNTED_RAW: always empty here (Drive folder listing is not possible "
        "with the current auth scope).\n\n"
        "Your job is the FUZZY layer only:\n\n"
        "1. STALE / PARTIAL: Flag fetched files that look present-but-stale or only "
        "partially filled. Compare date_range_in_data against the period and AS_OF date.\n\n"
        "2. DRIFT: Flag naming or machine/mould-code drift the deterministic matcher "
        "might have mishandled.\n\n"
        "3. UNACCOUNTED FILES: Skip — Drive listing unavailable.\n\n"
        "4. EXPLAIN: For each gap, give the likely plain-English reason.\n\n"
        "Hard rules:\n"
        "- COVERAGE is authoritative. Do NOT recompute counts or override it.\n"
        "- Every claim MUST cite the specific file_id or file_title it refers to.\n"
        "- Do not assert a file is missing unless it appears in coverage.not_found_at_all.\n"
        "- 'present_but_empty' is a CONTENT gap, label it as such, not as missing.\n"
        "- You are advisory. You do not approve, sign off, or modify any figure.\n"
        "- If unsure, say 'unverified' / use low confidence rather than guessing.\n\n"
        "Return ONLY valid JSON. No prose, no markdown, no code fences. Shape:\n"
        "{\n"
        '  "stale_or_partial": [\n'
        '    {"file_id": "...", "plant": "...", "month": "...",\n'
        '     "type": "stale|partial",\n'
        '     "evidence": "...", "suggested_action": "...",\n'
        '     "confidence": "high|medium|low"}\n'
        "  ],\n"
        '  "drift": [\n'
        '    {"file_id": "...", "type": "naming_drift|code_mismatch|other",\n'
        '     "evidence": "...", "suggested_action": "...",\n'
        '     "confidence": "high|medium|low"}\n'
        "  ],\n"
        '  "unaccounted_files": [],\n'
        '  "looks_complete": true,\n'
        '  "notes_for_engineer": "..."\n'
        "}"
    )

    user = (
        f"AS_OF: {as_of}\n\n"
        "MANIFEST:\n"
        + json.dumps(manifest_summary, indent=2, default=str)[:6000]
        + "\n\nCOVERAGE:\n"
        + json.dumps(coverage, indent=2, default=str)
        + "\n\nUNACCOUNTED_RAW: []\n"
        "(drive.file scope: folder enumeration is not possible — skip unaccounted_files.)"
    )

    try:
        prompt = f"{system}\n\n{user}"
        text, model_used = _create_text(FAST_MODEL, 1500, prompt)
        text = text.strip()
        # Strip accidental markdown fences
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()
        result = json.loads(text)
        result["_model"] = model_used
        return result
    except Exception as e:
        logger.warning("Advisory review failed (%s)", e)
        return None


def classify_downtime_reason(free_text: str) -> Optional[str]:
    """
    Map a free-text downtime note to a standard reason code using Claude.
    Returns None if API key missing or on error.
    This is for classification of TEXT only; no numeric data is involved.
    """
    if not _enabled() or not free_text.strip():
        return None

    standard_codes = [
        "Mould Change", "Material Change", "Breakdown - Hydraulic",
        "Breakdown - Electrical", "Die Head Change", "Colour Change",
        "Trial Run", "Power Failure", "Operator Absence", "Maintenance",
        "Setup", "Quality Issue", "Other",
    ]

    try:
        codes_list = ", ".join(f'"{c}"' for c in standard_codes)
        prompt = (
            f"Map this downtime note to one of these codes: {codes_list}.\n"
            f"Note: \"{free_text}\"\n"
            "Reply with ONLY the code, nothing else."
        )
        # Utility classification — always the fast tier.
        text, _ = _create_text(FAST_MODEL, 30, prompt)
        result = text.strip('"')
        if result in standard_codes:
            return result
        return None
    except Exception:
        return None
