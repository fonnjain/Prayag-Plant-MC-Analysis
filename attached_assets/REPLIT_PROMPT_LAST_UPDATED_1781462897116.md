# Add a "Last Updated" option to the period dropdown

Add a new entry to the time-span dropdown, labelled **"Last Updated"**, that automatically resolves to the **most recent date that actually has production data** — so the user lands on real figures instead of an empty in-progress day (e.g. the blank "yesterday = 15 June" case).

## 1. Behaviour
- "Last Updated" is **dynamic**, not a fixed range. When selected, the app:
  1. Finds the **latest date for which any plant has daily data** (the max production date across the daily files / fact store), and
  2. Sets the period to **that single day** (or, if you prefer a fuller view, that day plus the rolling window up to it — see option below).
- Show the resolved date in the sub-label, e.g. **"Last Updated: 13-06-2026"** (dd-mm-yyyy), so it's clear which day is being shown.
- Place it at the **top of the dropdown** (above Yesterday / Last 7 days / Last month / Custom), since it's the most useful default for "show me the freshest real numbers".

## 2. How to resolve "latest date with data"
- Query the **max date that has at least one non-empty production row** across the daily sources (per the daily-only source rule). A day with only blank/zero template rows does **not** count as data — use the same "has real rows" test the parser already uses (the one that distinguishes idle/empty from populated).
- If different plants have different latest dates (common — some log a day later), use the **overall max** and show per-plant freshness in the completeness panel ("Pipe to 13-Jun, PTMT to 12-Jun…"). Don't block on the laggards.
- If no plant has any data at all (shouldn't happen normally), fall back to the latest completed month and label it clearly.

## 3. Single-day vs window (pick one; single-day is the simpler default)
- **Default — single day:** period = the resolved latest date. Clean "freshest snapshot".
- **Optional — "Last Updated (rolling 7)":** if you'd rather show context, set the period to the 7 days ending on the resolved date. If you add this, keep it as a *separate* labelled entry so "Last Updated" stays an unambiguous single-day snapshot.

## 4. Guardrails (keep consistent with the existing rules)
- Resolution uses **daily data only** (do not consult the monthly summaries to decide the latest date).
- A day with no real rows is still shown honestly as "no production recorded" if somehow selected — never fabricate zeros. But "Last Updated" should *skip past* empty trailing days to the last populated one, which is the whole point.
- This is purely a **period-selection convenience** — it changes nothing about reading, computing, validation, or sign-off.

## 5. Acceptance criteria
- The dropdown shows "Last Updated" at the top; selecting it loads the most recent day that has real production data and shows that date in the sub-label.
- On a day where "Yesterday" would be empty (in-progress), "Last Updated" instead lands on the last populated day with publishable figures.
- The resolved date is computed from daily data only; per-plant freshness is visible in the completeness panel.
- No change to metrics, validation, or sign-off logic.
