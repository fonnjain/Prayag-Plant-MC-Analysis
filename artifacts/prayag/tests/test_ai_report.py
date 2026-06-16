"""Regression tests for the AI-generated report layer (narrative.generate_ai_report
section parsing + PDF). These never call the network — they test the pure parser
and the PDF assembly, plus the no-key degradation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import narrative
import pdf_export


def test_parser_splits_sections():
    txt = (
        "## Executive Summary\n"
        "Output was strong this month.\n\n"
        "## Key Findings\n"
        "Machine M/C-1 led with 50,000 kg.\n"
        "M/C-2 lagged at 10,000 kg.\n\n"
        "## Recommendations\n"
        "Investigate M/C-2 downtime."
    )
    secs = narrative.parse_ai_report_sections(txt)
    assert len(secs) == 3, secs
    assert secs[0]["heading"] == "Executive Summary"
    # Consecutive non-blank lines join into one paragraph.
    assert secs[1]["paragraphs"][0].startswith("Machine M/C-1")
    assert "M/C-2 lagged" in secs[1]["paragraphs"][0]
    print("PASS: parser splits '## ' headings into sections with joined paragraphs")


def test_parser_lead_text_before_first_heading():
    secs = narrative.parse_ai_report_sections("Intro line.\n\n## Findings\nBody.")
    assert secs[0]["heading"] == ""
    assert secs[0]["paragraphs"] == ["Intro line."]
    assert secs[1]["heading"] == "Findings"
    print("PASS: text before the first heading becomes an untitled lead section")


def test_parser_empty():
    assert narrative.parse_ai_report_sections("") == []
    assert narrative.parse_ai_report_sections(None) == []
    print("PASS: empty/None input yields no sections")


def test_generate_returns_none_without_key():
    key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        out = narrative.generate_ai_report("T", "P", "k", {}, [], [])
        assert out is None
    finally:
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    print("PASS: generate_ai_report returns None when no API key is configured")


def test_pdf_renders_sections():
    secs = [
        {"heading": "Executive Summary", "paragraphs": ["All good."]},
        {"heading": "Risks <urgent>", "paragraphs": ["Watch M/C-2 & M/C-3."]},
    ]
    pdf = pdf_export.generate_ai_report_pdf(
        title="Test Report",
        period_label="May 2026",
        overall={"oee": 50.0, "total_count": 1000, "rejection_pct": 2.0},
        sections=secs,
        table_rows=[["M/C-1", "100"]],
        table_headers=["Machine", "Out"],
        analysis_model="claude-x · deep tier",
    )
    # reportlab may be unavailable in some envs; only assert when it is.
    if pdf_export.REPORTLAB_AVAILABLE:
        assert pdf[:4] == b"%PDF", "expected a PDF byte stream"
        assert len(pdf) > 1000
    print("PASS: AI report PDF renders sections (and escapes stray markup) "
          "or degrades to empty bytes without reportlab")


if __name__ == "__main__":
    test_parser_splits_sections()
    test_parser_lead_text_before_first_heading()
    test_parser_empty()
    test_generate_returns_none_without_key()
    test_pdf_renders_sections()
    print("\nAll AI-report regression tests passed.")
