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
            model="claude-3-5-haiku-20241022",
            max_tokens=300,
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
            model="claude-3-5-haiku-20241022",
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
