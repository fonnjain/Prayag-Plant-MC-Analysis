"""
PDF export using reportlab — generates a report PDF from computed metrics.
No AI involved; numbers come straight from the metrics engine.
"""
from __future__ import annotations
import io
from typing import List, Dict, Any

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, HRFlowable, PageBreak,
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

NAVY = (0x1F / 255, 0x38 / 255, 0x64 / 255)
TERRA = (0xC5 / 255, 0x5A / 255, 0x11 / 255)
GREEN = (0x22 / 255, 0xC5 / 255, 0x5E / 255)
AMBER = (0xF5 / 255, 0x9E / 255, 0x0B / 255)
RED = (0xEF / 255, 0x44 / 255, 0x44 / 255)


def _rating_color(oee_pct: float):
    if oee_pct >= 85:
        return colors.Color(*GREEN)
    elif oee_pct >= 60:
        return colors.Color(*AMBER)
    return colors.Color(*RED)


def _confirm_status_bits(status: str, counts: Dict[str, Any]):
    """Map a four-tier confirmation status → (label, reportlab color)."""
    total = (counts or {}).get("total", 0)
    if status == "error":
        return f"⚠ Needs review — {total} issue(s)", colors.Color(*RED)
    if status == "warning":
        return f"⚠ {total} flag(s)", colors.Color(*AMBER)
    return "✓ Data reconciled — all checks passed", colors.Color(*GREEN)


def generate_report_pdf(
    title: str,
    period_label: str,
    overall: Dict[str, Any],
    table_rows: List[List[str]],
    table_headers: List[str],
    narrative: str | None = None,
    validation_status: Dict | None = None,
    confirmation: Dict | None = None,
    confirmation_summary: str | None = None,
) -> bytes:
    """Return PDF bytes for a report page."""
    if not REPORTLAB_AVAILABLE:
        return b""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", fontSize=16, textColor=colors.Color(*NAVY),
        spaceAfter=4, fontName="Helvetica-Bold", alignment=TA_LEFT,
    )
    sub_style = ParagraphStyle(
        "sub", fontSize=10, textColor=colors.Color(*TERRA),
        spaceAfter=2, fontName="Helvetica",
    )
    body_style = ParagraphStyle(
        "body", fontSize=9, textColor=colors.black,
        spaceAfter=6, fontName="Helvetica", leading=13,
    )

    story = []

    story.append(Paragraph("Prayag Production Analytics", title_style))
    story.append(Paragraph(title, ParagraphStyle(
        "h2", fontSize=13, textColor=colors.Color(*TERRA),
        spaceAfter=2, fontName="Helvetica-Bold",
    )))
    story.append(Paragraph(f"Period: {period_label}", sub_style))
    story.append(HRFlowable(width="100%", thickness=1,
                             color=colors.Color(*NAVY), spaceAfter=8))

    # Data Confirmation (four-tier deterministic audit) — primary trust signal.
    if confirmation:
        status_text, status_color = _confirm_status_bits(
            confirmation.get("status", "pass"), confirmation.get("counts"),
        )
        story.append(Paragraph("Data Confirmation", ParagraphStyle(
            "conf_h", fontSize=11, textColor=colors.Color(*NAVY),
            spaceAfter=3, fontName="Helvetica-Bold",
        )))
        story.append(Paragraph(status_text, ParagraphStyle(
            "conf_status", fontSize=10, textColor=status_color,
            spaceAfter=2, fontName="Helvetica-Bold",
        )))
        score_label = confirmation.get("score_label")
        if score_label:
            story.append(Paragraph(
                f"Completeness: {score_label}",
                ParagraphStyle("conf_score", fontSize=8.5,
                               textColor=colors.Color(*NAVY),
                               spaceAfter=4, fontName="Helvetica"),
            ))
        if confirmation_summary:
            story.append(Paragraph(
                confirmation_summary,
                ParagraphStyle("conf_sum", fontSize=8.5, textColor=colors.black,
                               spaceAfter=4, fontName="Helvetica", leading=12),
            ))

        # Key issues: errors first, then warnings; cap the list to keep it tidy.
        issues = confirmation.get("issues") or []
        ordered = (
            [i for i in issues if i.get("severity") == "error"]
            + [i for i in issues if i.get("severity") != "error"]
        )
        max_show = 8
        issue_style = ParagraphStyle(
            "conf_issue", fontSize=8, textColor=colors.black,
            spaceAfter=2, fontName="Helvetica", leading=11,
        )
        for i in ordered[:max_show]:
            sev = "✗" if i.get("severity") == "error" else "•"
            sev_color = RED if i.get("severity") == "error" else AMBER
            tier = i.get("tier_label", "")
            scope = " ".join(
                p for p in [i.get("plant", ""), i.get("machine", "")] if p
            )
            scope_txt = f"{scope} — " if scope else ""
            msg = i.get("message", "")
            hex_col = "#%02X%02X%02X" % (
                int(sev_color[0] * 255), int(sev_color[1] * 255),
                int(sev_color[2] * 255),
            )
            story.append(Paragraph(
                f'<font color="{hex_col}">{sev}</font> '
                f"<b>[{tier}]</b> {scope_txt}{msg}",
                issue_style,
            ))
        remaining = len(ordered) - max_show
        if remaining > 0:
            story.append(Paragraph(
                f"…and {remaining} more issue(s) — see the in-app Data Confirmation page.",
                ParagraphStyle("conf_more", fontSize=8,
                               textColor=colors.grey, spaceAfter=2,
                               fontName="Helvetica-Oblique"),
            ))
        if confirmation.get("status") == "pass" and not ordered:
            story.append(Paragraph(
                "All four tiers (completeness, reconciliation, validity, plausibility) passed.",
                issue_style,
            ))
        story.append(Spacer(1, 4 * mm))

    # Validation status (legacy reconciliation) — fallback when no confirmation.
    elif validation_status:
        if validation_status.get("reconciled"):
            status_text = f"✓ Data reconciled | Flags: {validation_status.get('flag_count', 0)}"
            status_color = colors.Color(*GREEN)
        else:
            status_text = f"⚠ Reconciliation issues | Flags: {validation_status.get('flag_count', 0)}"
            status_color = colors.Color(*RED)
        story.append(Paragraph(status_text, ParagraphStyle(
            "status", fontSize=9, textColor=status_color,
            spaceAfter=6, fontName="Helvetica",
        )))

    # Overall KPIs
    if overall:
        kpi_data = [
            ["OEE", f"{overall.get('oee', 0):.1f}%"],
            ["Availability", f"{overall.get('availability', 0):.1f}%"],
            ["Performance", f"{overall.get('performance', 0):.1f}%"],
            ["Quality", f"{overall.get('quality', 0):.1f}%"],
            ["Total Output", f"{overall.get('total_count', 0):,.0f}"],
            ["Rejection %", f"{overall.get('rejection_pct', 0):.2f}%"],
            ["Plan Attainment", f"{overall.get('attainment', 0):.1f}%"],
        ]
        kpi_table = Table(kpi_data, colWidths=[60 * mm, 50 * mm])
        oee_val = overall.get("oee", 0)
        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(*NAVY)),
            ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.95, 0.95, 0.97)),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1),
             [colors.white, colors.Color(0.97, 0.97, 0.99)]),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 8 * mm))

    # Narrative
    if narrative:
        story.append(Paragraph("Management Commentary", ParagraphStyle(
            "h3", fontSize=11, textColor=colors.Color(*NAVY),
            spaceAfter=3, fontName="Helvetica-Bold",
        )))
        story.append(Paragraph(narrative, body_style))
        story.append(Spacer(1, 4 * mm))

    # Data table
    if table_rows and table_headers:
        story.append(Paragraph("Detailed Data", ParagraphStyle(
            "h3", fontSize=11, textColor=colors.Color(*NAVY),
            spaceAfter=3, fontName="Helvetica-Bold",
        )))
        col_w = (180 * mm) / max(len(table_headers), 1)
        col_widths = [col_w] * len(table_headers)

        table_data = [table_headers] + table_rows
        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(*NAVY)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (1, 0), (-1, -1),
             [colors.white, colors.Color(0.97, 0.97, 0.99)]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(tbl)

    # Appendix: every data-confirmation issue, so the PDF is self-describing
    # for an auditor who only has the document (no in-app access).
    if confirmation:
        all_issues = confirmation.get("issues") or []
        if all_issues:
            story.append(PageBreak())
            story.append(Paragraph(
                "Data Confirmation — all issues", ParagraphStyle(
                    "app_h", fontSize=13, textColor=colors.Color(*TERRA),
                    spaceAfter=2, fontName="Helvetica-Bold",
                ),
            ))
            err_n = sum(1 for i in all_issues if i.get("severity") == "error")
            warn_n = len(all_issues) - err_n
            story.append(Paragraph(
                f"{len(all_issues)} issue(s) in total — "
                f"{err_n} error(s), {warn_n} warning(s)/flag(s). "
                "Grouped by check tier; errors listed first within each tier.",
                ParagraphStyle("app_sub", fontSize=8.5,
                               textColor=colors.Color(*NAVY),
                               spaceAfter=6, fontName="Helvetica"),
            ))
            story.append(HRFlowable(width="100%", thickness=0.8,
                                    color=colors.Color(*NAVY), spaceAfter=6))

            app_issue_style = ParagraphStyle(
                "app_issue", fontSize=8, textColor=colors.black,
                spaceAfter=2, fontName="Helvetica", leading=11,
            )
            tier_h_style = ParagraphStyle(
                "app_tier_h", fontSize=10, textColor=colors.Color(*NAVY),
                spaceBefore=6, spaceAfter=3, fontName="Helvetica-Bold",
            )

            # Stable tier order from first appearance, errors before warnings.
            tier_order: List[Any] = []
            by_tier: Dict[Any, List[Dict[str, Any]]] = {}
            for i in all_issues:
                t = i.get("tier")
                if t not in by_tier:
                    by_tier[t] = []
                    tier_order.append(t)
                by_tier[t].append(i)

            for t in tier_order:
                group = by_tier[t]
                tier_label = group[0].get("tier_label", str(t))
                story.append(Paragraph(
                    f"{tier_label} ({len(group)})", tier_h_style,
                ))
                ordered_group = (
                    [i for i in group if i.get("severity") == "error"]
                    + [i for i in group if i.get("severity") != "error"]
                )
                for i in ordered_group:
                    sev = "✗" if i.get("severity") == "error" else "•"
                    sev_color = RED if i.get("severity") == "error" else AMBER
                    scope = " ".join(
                        p for p in [i.get("plant", ""), i.get("machine", "")] if p
                    )
                    scope_txt = f"{scope} — " if scope else ""
                    msg = i.get("message", "")
                    hex_col = "#%02X%02X%02X" % (
                        int(sev_color[0] * 255), int(sev_color[1] * 255),
                        int(sev_color[2] * 255),
                    )
                    story.append(Paragraph(
                        f'<font color="{hex_col}">{sev}</font> '
                        f"{scope_txt}{msg}",
                        app_issue_style,
                    ))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "Generated by Prayag Production Analytics — numbers computed by deterministic engine, never through AI.",
        ParagraphStyle("footer", fontSize=7, textColor=colors.grey),
    ))

    doc.build(story)
    return buf.getvalue()
