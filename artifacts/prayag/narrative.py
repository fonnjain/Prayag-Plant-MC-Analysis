"""
Optional Claude narrative — used ONLY to write prose from already-computed numbers.
Claude never reads, transcribes, or recomputes any figure.
Requires ANTHROPIC_API_KEY in environment. Cache per (period, view).
"""
from __future__ import annotations
import os
import hashlib
import json
from typing import Optional

_cache: dict[str, str] = {}


def _enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _cache_key(view: str, period_key: str, metrics_summary: dict) -> str:
    payload = json.dumps({"view": view, "period": period_key, "metrics": metrics_summary}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_narrative(
    view: str,
    period_label: str,
    period_key: str,
    metrics_summary: dict,
    extra_context: str = "",
) -> Optional[str]:
    """
    Return a Claude-written narrative paragraph from pre-computed metrics.
    Returns None if ANTHROPIC_API_KEY is not set or on any error.
    Claude receives only the already-computed numbers, never raw sheet data.
    """
    if not _enabled():
        return None

    ck = _cache_key(view, period_key, metrics_summary)
    if ck in _cache:
        return _cache[ck]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        _cache[ck] = text
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
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        prompt = (
            "You match factory machine codes between two lists. These are CODES "
            "(names), not numbers — never invent a match.\n"
            f"Data codes: {json.dumps(unmatched)}\n"
            f"Master codes: {json.dumps(master_codes)}\n"
            "Return ONLY a JSON object mapping each data code that confidently "
            "refers to the same physical machine as a master code, to that master "
            "code. Omit any data code with no confident match. No prose."
        )
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
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
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        issues_text = "\n".join(f"  - {s}" for s in issues_brief[:25]) or "  (none)"
        prompt = f"""You are summarising a data-confirmation report for a factory production dashboard.
Overall status: {status}
Completeness score: {score_label}
Issues found (already detected; do not recalculate or judge the numbers):
{issues_text}

Write 2-3 plain-English sentences for a factory manager explaining whether the data
can be trusted, what is missing or flagged, and what to check. Do not invent figures.
Do not use markdown. Be concise and factual."""
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=260,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        _cache[ck] = text
        return text
    except Exception:
        return None


def claude_sanity_check(
    confirmation: dict,
    computed_metrics: dict,
    period_label: str,
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

    fingerprint = confirmation.get("fingerprint", "")
    ck = "sanity:" + fingerprint
    if ck in _cache:
        return _cache[ck]

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        _cache[ck] = text
        return text
    except Exception:
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
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        codes_list = ", ".join(f'"{c}"' for c in standard_codes)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    f"Map this downtime note to one of these codes: {codes_list}.\n"
                    f"Note: \"{free_text}\"\n"
                    "Reply with ONLY the code, nothing else."
                ),
            }],
        )
        result = message.content[0].text.strip().strip('"')
        if result in standard_codes:
            return result
        return None
    except Exception:
        return None
