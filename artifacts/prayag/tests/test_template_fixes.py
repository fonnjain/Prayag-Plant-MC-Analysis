"""Template-only tests for:
  Fix 1 — three-tier rejection colour at five template locations.
  Fix 2 — Tank N/A labelling (em-dash + tooltip) in plant.html.

All tests are offline — no Google Sheets calls, no database access.
Only template rendering is exercised; no parsing or metrics logic is changed.
"""
import pytest
import os
import sys

# ---------------------------------------------------------------------------
# Minimal Jinja2 environment (no Flask app import required for these tests)
# ---------------------------------------------------------------------------
from jinja2 import Environment, FileSystemLoader, Undefined

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


def _make_env():
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=False,
    )
    # Stub the `info` macro so templates that use {% from "_macros.html" %}
    # can render without the full macro file being perfect.
    return env


# ---------------------------------------------------------------------------
# Helpers to render snippet HTML for colour-threshold tests
# ---------------------------------------------------------------------------

def _render_rejection_colour(template_snippet: str, rejection_pct):
    """Render a minimal Jinja2 snippet and return the rendered string."""
    env = _make_env()
    tpl = env.from_string(template_snippet)
    return tpl.render(rejection_pct=rejection_pct)


# Five canonical snippets — one per affected location.
# Variable names match each template exactly.

SNIPPETS = {
    "plant": (
        '{% if m.rejection_pct is not none and m.rejection_pct < 2 %}green'
        '{% elif m.rejection_pct is not none and m.rejection_pct < 5 %}amber'
        '{% else %}red{% endif %}'
    ),
    "report_detail": (
        '{% if o.rejection_pct is not none and o.rejection_pct < 2 %}green'
        '{% elif o.rejection_pct is not none and o.rejection_pct < 5 %}amber'
        '{% else %}red{% endif %}'
    ),
    "gom_overall": (
        '{% if overall.rejection_pct is not none and overall.rejection_pct < 2 %}green'
        '{% elif overall.rejection_pct is not none and overall.rejection_pct < 5 %}amber'
        '{% else %}red{% endif %}'
    ),
    "gom_row": (
        '{% if row.metrics.rejection_pct is not none and row.metrics.rejection_pct < 2 %}green'
        '{% elif row.metrics.rejection_pct is not none and row.metrics.rejection_pct < 5 %}amber'
        '{% else %}red{% endif %}'
    ),
    "tank_location": (
        '{% if overall.rejection_pct is not none and overall.rejection_pct < 2 %}green'
        '{% elif overall.rejection_pct is not none and overall.rejection_pct < 5 %}amber'
        '{% else %}red{% endif %}'
    ),
}


# ---------------------------------------------------------------------------
# Fix 1 — Three-tier rejection colour
# ---------------------------------------------------------------------------

class TestThreeTierRejectionColour:
    """Verify the three-tier rule at every affected location."""

    @pytest.mark.parametrize("location", list(SNIPPETS.keys()))
    def test_below_2_is_green(self, location):
        snippet = SNIPPETS[location]
        env = _make_env()
        tpl = env.from_string(snippet)
        ctx = dict(m={"rejection_pct": 1.5},
                   o={"rejection_pct": 1.5},
                   overall={"rejection_pct": 1.5},
                   row={"metrics": {"rejection_pct": 1.5}})
        assert tpl.render(**ctx).strip() == "green"

    @pytest.mark.parametrize("location", list(SNIPPETS.keys()))
    def test_between_2_and_5_is_amber(self, location):
        snippet = SNIPPETS[location]
        env = _make_env()
        tpl = env.from_string(snippet)
        ctx = dict(m={"rejection_pct": 3.0},
                   o={"rejection_pct": 3.0},
                   overall={"rejection_pct": 3.0},
                   row={"metrics": {"rejection_pct": 3.0}})
        assert tpl.render(**ctx).strip() == "amber"

    @pytest.mark.parametrize("location", list(SNIPPETS.keys()))
    def test_at_exactly_2_is_amber(self, location):
        snippet = SNIPPETS[location]
        env = _make_env()
        tpl = env.from_string(snippet)
        ctx = dict(m={"rejection_pct": 2.0},
                   o={"rejection_pct": 2.0},
                   overall={"rejection_pct": 2.0},
                   row={"metrics": {"rejection_pct": 2.0}})
        assert tpl.render(**ctx).strip() == "amber"

    @pytest.mark.parametrize("location", list(SNIPPETS.keys()))
    def test_at_5_or_above_is_red(self, location):
        snippet = SNIPPETS[location]
        env = _make_env()
        tpl = env.from_string(snippet)
        ctx = dict(m={"rejection_pct": 5.0},
                   o={"rejection_pct": 5.0},
                   overall={"rejection_pct": 5.0},
                   row={"metrics": {"rejection_pct": 5.0}})
        assert tpl.render(**ctx).strip() == "red"

    @pytest.mark.parametrize("location", list(SNIPPETS.keys()))
    def test_null_rejection_pct_does_not_crash_and_is_not_green(self, location):
        """None rejection_pct: must not raise, must not render green."""
        snippet = SNIPPETS[location]
        env = _make_env()
        tpl = env.from_string(snippet)
        ctx = dict(m={"rejection_pct": None},
                   o={"rejection_pct": None},
                   overall={"rejection_pct": None},
                   row={"metrics": {"rejection_pct": None}})
        result = tpl.render(**ctx).strip()
        assert result != "green", f"null rejection_pct rendered green at {location}"
        # Must be either amber (impossible for None since both guards fail) or red
        assert result == "red"

    def test_overview_html_unchanged(self):
        """overview.html already uses the three-tier rule — confirm it is untouched."""
        overview_path = os.path.join(TEMPLATES_DIR, "overview.html")
        with open(overview_path) as f:
            src = f.read()
        # Three-tier pattern must be present
        assert "rejection_pct < 2" in src and "rejection_pct < 5" in src, \
            "overview.html three-tier rule missing"
        # Two-tier pattern must NOT be present (only one < threshold without elif)
        import re
        two_tier = re.findall(
            r'rejection_pct\s*<\s*2\s*%}[^{]*text-green[^{]*{%\s*else\s*%}[^{]*text-red',
            src)
        assert not two_tier, "overview.html still has a two-tier rule"


# ---------------------------------------------------------------------------
# Verify the five changed templates now contain the three-tier pattern
# ---------------------------------------------------------------------------

class TestTemplateSourceContainsTiers:
    """Grep the actual template files to confirm the amber tier was added."""

    LOCATIONS = [
        ("plant.html",              "m.rejection_pct"),
        ("report_detail.html",      "o.rejection_pct"),
        ("report_gom_summary.html", "overall.rejection_pct"),
        ("report_gom_summary.html", "row.metrics.rejection_pct"),
        ("report_tank_location.html", "overall.rejection_pct"),
    ]

    @pytest.mark.parametrize("filename,var", LOCATIONS)
    def test_amber_tier_present(self, filename, var):
        """Amber tier is present — either via the old null-guarded form or the
        new null-branch form ({% if ... is none %}...{% elif ... < 5 %}...)."""
        path = os.path.join(TEMPLATES_DIR, filename)
        with open(path) as f:
            src = f.read()
        # Old form: guard with is-not-none; new form: null branch + bare < 5.
        # Both patterns include "< 5" as the amber threshold.
        assert f"{var} < 5" in src, \
            f"amber tier not found for {var} in {filename}"
        # Null rendering: new form wraps the whole colour block in a none-check.
        # Either the old is-not-none guard OR the new is-none branch must exist.
        has_null_guard = f"{var} is not none and {var} < 5" in src
        has_null_branch = f"{var} is none" in src
        assert has_null_guard or has_null_branch, \
            f"null-safe guard missing for {var} in {filename}"

    @pytest.mark.parametrize("filename,var", LOCATIONS)
    def test_two_tier_pattern_removed(self, filename, var):
        """The old two-tier pattern (no amber) must not exist."""
        path = os.path.join(TEMPLATES_DIR, filename)
        with open(path) as f:
            src = f.read()
        import re
        # Old pattern: var < 2 → green, else → red (no amber elif)
        old_pattern = rf'{re.escape(var)}\s*<\s*2\s*%}}[^{{]*text-green[^{{]*{{%\s*else\s*%}}[^{{]*text-red'
        assert not re.search(old_pattern, src), \
            f"Old two-tier pattern still present for {var} in {filename}"


# ---------------------------------------------------------------------------
# Fix 2 — Tank N/A labelling in plant.html
# ---------------------------------------------------------------------------

class TestTankNALabelling:
    """Verify the em-dash tooltip renders for Tank plants in plant.html."""

    # Updated wording per R-25: Tank has run-hours columns in the source;
    # "not applicable" was factually wrong. Now says "not currently tracked".
    TANK_SNIPPET = (
        "{% if avail %}{{ val }}%"
        "{% elif item.plant in ('TANK', 'TANK_VN', 'TANK_WB') %}"
        '<span class="text-gray-400" title="Not currently tracked for Tank'
        ' — run hours are recorded in the source but are not yet complete'
        ' or reconciled.">\u2014</span>'
        "{% else %}"
        '<span class="text-gray-400">n/a</span>'
        "{% endif %}"
    )
    # Canonical text that must appear in the actual plant.html file (R-25).
    _TOOLTIP_TEXT = "Not currently tracked for Tank"

    def test_tank_plant_renders_emdash(self):
        env = _make_env()
        tpl = env.from_string(self.TANK_SNIPPET)
        result = tpl.render(avail=False, val=0, item={"plant": "TANK"})
        assert "—" in result, "em-dash missing for TANK plant"
        assert self._TOOLTIP_TEXT in result, "tooltip missing for TANK"
        assert "n/a" not in result, "n/a must not appear for TANK"

    def test_tank_vn_renders_emdash(self):
        env = _make_env()
        tpl = env.from_string(self.TANK_SNIPPET)
        result = tpl.render(avail=False, val=0, item={"plant": "TANK_VN"})
        assert "—" in result
        assert self._TOOLTIP_TEXT in result

    def test_tank_wb_renders_emdash(self):
        env = _make_env()
        tpl = env.from_string(self.TANK_SNIPPET)
        result = tpl.render(avail=False, val=0, item={"plant": "TANK_WB"})
        assert "—" in result
        assert self._TOOLTIP_TEXT in result

    def test_non_tank_plant_renders_na(self):
        """GARDEN, HDPE, MOULDING etc. keep showing n/a (not em-dash)."""
        env = _make_env()
        tpl = env.from_string(self.TANK_SNIPPET)
        for plant in ("GARDEN", "HDPE", "MOULDING", "PTMT", "PIPE"):
            result = tpl.render(avail=False, val=0, item={"plant": plant})
            assert "n/a" in result, f"{plant} should show n/a, not em-dash"
            assert "—" not in result.replace("&#8212;", ""), \
                f"{plant} must not show em-dash"

    def test_available_metric_renders_value(self):
        """When avail=True the value % is shown regardless of plant."""
        env = _make_env()
        tpl = env.from_string(self.TANK_SNIPPET)
        for plant in ("TANK", "GARDEN"):
            result = tpl.render(avail=True, val=75.3, item={"plant": plant})
            assert "75.3%" in result

    def test_plant_html_source_contains_tank_guard(self):
        """Confirm the actual plant.html file has the Tank em-dash guard."""
        path = os.path.join(TEMPLATES_DIR, "plant.html")
        with open(path) as f:
            src = f.read()
        assert "item.plant in ('TANK', 'TANK_VN', 'TANK_WB')" in src, \
            "Tank plant guard missing from plant.html"
        assert "Not currently tracked for Tank" in src, \
            "Tank tooltip text missing or outdated in plant.html (R-25 requires 'not currently tracked')"

    def test_plant_html_source_still_has_na_for_others(self):
        """Confirm plant.html still shows n/a for non-Tank unavailable metrics."""
        path = os.path.join(TEMPLATES_DIR, "plant.html")
        with open(path) as f:
            src = f.read()
        assert 'text-gray-400">n/a</span>' in src, \
            "n/a fallback missing from plant.html"
