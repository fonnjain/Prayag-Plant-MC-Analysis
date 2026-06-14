"""Regression test: the PDF's "Data Confirmation — all issues" appendix must
list EVERY flagged issue, never a truncated/capped subset.

The page-1 Data Confirmation block intentionally caps its "key issues" preview
(``max_show``), but the appendix on the final page is the auditor's complete
record. A future layout change could silently re-cap or drop issues there; this
test locks in one-rendered-line-per-issue across every tier and severity.

We capture the text handed to every reportlab ``Paragraph`` (the content that
gets rendered) rather than re-parsing the compressed PDF binary — same guarantee,
no extra dependency. The test fails if any issue message goes missing.

Run: cd artifacts/prayag && python3 -m tests.test_pdf_all_issues
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdf_export
from pdf_export import generate_report_pdf, REPORTLAB_AVAILABLE


def _make_issues():
    """Many mixed-severity issues spread across all four tiers — more than the
    page-1 preview cap (``max_show`` = 8) so a cap would be caught."""
    tiers = [
        (1, "Tier 1 · Completeness"),
        (2, "Tier 2 · Reconciliation"),
        (3, "Tier 3 · Validity"),
        (4, "Tier 4 · Plausibility"),
    ]
    issues = []
    n = 0
    for tier, tier_label in tiers:
        # 3 errors + 3 warnings per tier = 24 issues total (well over the cap).
        for severity in ("error", "error", "error", "warning", "warning", "warning"):
            n += 1
            issues.append({
                "tier": tier,
                "tier_label": tier_label,
                "severity": severity,
                "plant": f"PLANT{n}",
                "machine": f"M/C-{n}",
                "message": f"UNIQUE_ISSUE_MARKER_{n:03d} in {tier_label}",
                "issue_key": f"key-{n}",
            })
    return issues


def _capture_rendered_text(**kwargs):
    """Render a PDF, capturing the text of every Paragraph flowable built."""
    captured = []
    real_paragraph = pdf_export.Paragraph

    def _recording_paragraph(text, *args, **kw):
        captured.append(text)
        return real_paragraph(text, *args, **kw)

    pdf_export.Paragraph = _recording_paragraph
    try:
        pdf_bytes = generate_report_pdf(**kwargs)
    finally:
        pdf_export.Paragraph = real_paragraph
    return pdf_bytes, "\n".join(captured)


def _base_kwargs(confirmation):
    return dict(
        title="Overview",
        period_label="FY 2025-26",
        overall={"oee": 72.0, "availability": 80.0, "performance": 90.0,
                 "quality": 99.0, "total_count": 12345, "rejection_pct": 1.2,
                 "attainment": 88.0},
        table_rows=[["PIPE", "72.0%"]],
        table_headers=["Plant", "OEE"],
        confirmation=confirmation,
    )


def test_appendix_lists_every_issue_across_all_tiers():
    issues = _make_issues()
    confirmation = {
        "status": "error",
        "score_label": "78% complete",
        "counts": {"total": len(issues)},
        "issues": issues,
    }

    pdf_bytes, rendered = _capture_rendered_text(**_base_kwargs(confirmation))

    assert REPORTLAB_AVAILABLE, "reportlab must be installed for this test"
    assert pdf_bytes, "PDF generation returned empty bytes"

    missing = [i["message"] for i in issues if i["message"] not in rendered]
    assert not missing, (
        f"{len(missing)} issue(s) never rendered into the PDF — the appendix "
        f"truncated or dropped issues: {missing}"
    )
    print(f"PASS: all {len(issues)} issues across 4 tiers rendered into the PDF")


def test_appendix_not_capped_by_page1_preview_limit():
    """Directly guard the regression: with 24 issues the page-1 preview caps at
    ``max_show`` and adds a '…and N more' line, but the appendix must still
    render all 24. Asserting every marker is present proves no cap leaked into
    the appendix."""
    issues = _make_issues()
    assert len(issues) > 8, "fixture must exceed the page-1 preview cap"

    confirmation = {
        "status": "error",
        "counts": {"total": len(issues)},
        "issues": issues,
    }
    _, rendered = _capture_rendered_text(**_base_kwargs(confirmation))

    present = sum(1 for i in issues if i["message"] in rendered)
    assert present == len(issues), (
        f"only {present}/{len(issues)} issue markers rendered — appendix is capped"
    )
    print(f"PASS: appendix rendered all {len(issues)} issues despite page-1 cap")


def test_appendix_handles_issues_missing_optional_fields():
    """Issues without plant/machine/tier_label must still render their message."""
    issues = [
        {"tier": 2, "severity": "warning",
         "message": "BARE_ISSUE_no_scope_no_tier_label"},
        {"tier": 3, "tier_label": "Tier 3 · Validity", "severity": "error",
         "plant": "PIPE", "message": "SCOPED_ISSUE_plant_only"},
    ]
    confirmation = {"status": "error", "counts": {"total": len(issues)},
                    "issues": issues}
    _, rendered = _capture_rendered_text(**_base_kwargs(confirmation))

    for i in issues:
        assert i["message"] in rendered, f"issue dropped: {i['message']}"
    print("PASS: issues missing optional fields still render their message")


if __name__ == "__main__":
    test_appendix_lists_every_issue_across_all_tiers()
    test_appendix_not_capped_by_page1_preview_limit()
    test_appendix_handles_issues_missing_optional_fields()
    print("\nAll PDF all-issues regression tests passed.")
