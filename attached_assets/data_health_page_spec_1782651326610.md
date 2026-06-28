# Build Spec: Data Health page

**Goal:** A single `/data` page that answers, at a glance, "is the data fresh, and where
are the gaps?" — last input date per plant/machine, what has no data, and source-workbook
health. Minimalist, existing brand (Navy `#1F3864`, Terracotta `#C55A11`, DD-MM-YYYY,
mobile-first). It is the UI surface of the ingest manifest the pipeline already builds.

---

## 1. Page sections (top to bottom)

1. **Freshness banner (navy).** Title "Data health", today's date, last sync time,
   "N workbooks tracked". This is the always-visible header.

2. **Four summary cards.** Latest data input (date + which plant + days behind) ·
   Plants reporting (X of N) · Files with no data (count) · Machines idle (X of N).

3. **Last data input by plant** (the headline ask). Table: Plant · Location · Last input
   date · Machines (reporting/total) · Status pill.

4. **No data — needs a look.** Three grouped cards:
   - Empty workbooks (file present, zero rows).
   - Idle machines (in roster, no run this window).
   - Roster gaps (mould/machine codes in master roster with no data — "gap, not zero").

5. **Source workbooks.** Table: Workbook · Last modified · Data through · Rows.
   Footer line: "No spreadsheet changes detected in the last N days for …".

---

## 2. Status classification — the core rule

Status keys off **date relative to today AND to the last-entered date**, never emptiness
alone. (Same rule as the 15-June "not-an-error" fix — reuse that logic.)

| Status | Pill | When |
|---|---|---|
| current | green | last input within ~2 days of today/expected |
| lagging Nd | amber | a few days behind, but a past closed period still has data |
| empty | red | a PAST, closed period with zero rows where data is expected |
| awaiting | gray | period is today/future/after last-entered, OR site is summary-grain (VN/WB) |

Hard rule: never show **red/error** for today, a future date, or a date after the plant's
last-entered date — that's "awaiting entry", gray. Per-plant cutoffs (PTMT 14-06, Pipe 04-06,
etc.) are evaluated **per plant**, not one global date.

---

## 3. Data source — reuse the ingest manifest

Every value on this page comes from the manifest the pipeline already builds at fetch time
(see the manifest spec). Do NOT recompute from scratch. Per `fetched` entry the manifest has:
`plant, location, file_id, file_title, modified_time, rows_found, date_range_in_data,
columns_seen, aggregates`. The page reads:

- **Last input by plant** = `max(date_range_in_data)` per plant.
- **Days behind** = today − last input (per plant).
- **Files with no data** = entries where `rows_found == 0`.
- **Machines idle / roster gaps** = from the Tier-1 completeness output already computed
  (never-reporting machines, roster moulds with no data).
- **Source workbooks table** = `file_title, modified_time, max(date_range_in_data), rows_found`.
- **"No changes in N days"** = compare `modified_time` to now.

If the manifest isn't persisted yet, persist it per refresh (the spec already recommends this);
the page is just a read view over it.

---

## 4. Design (minimalist)

- Reuse existing components: navy banner, summary metric cards, status pills, tables.
- Tables: hairline `0.5px` row borders, 12px radius container, no zebra, no heavy chrome.
- "No data" cards use a single coloured left border (terracotta = empty, amber = idle,
  gray = roster gap), square corners on the accented side.
- Sentence case, DD-MM-YYYY everywhere, no emoji.
- Mobile-first: tables collapse to stacked rows under ~640px; summary cards wrap 2x2.

---

## 5. Replit prompt (paste-ready)

```
Add a new /data ("Data health") page. It is a read view over the ingest
manifest the pipeline already builds — do not recompute metrics from source.

Sections, top to bottom:
1) Navy freshness banner: "Data health", today's date (DD-MM-YYYY), last sync
   time, "<N> workbooks tracked".
2) Four summary cards: Latest data input (date + plant + days behind);
   Plants reporting (X of N); Files with no data (count); Machines idle (X/N).
3) "Last data input by plant" table: Plant | Location | Last input | Machines
   (reporting/total) | Status pill. One row per plant incl. VN/WB.
4) "No data — needs a look": three cards — Empty workbooks (rows_found==0),
   Idle machines (roster, no run this window), Roster gaps (codes in master
   roster with no data).
5) "Source workbooks" table: Workbook | Last modified | Data through | Rows,
   plus a footer "No spreadsheet changes detected in the last N days for ...".

Status rule (reuse the 15-June not-an-error logic): classify by date-vs-today
AND date-vs-last-entered, per plant — never by emptiness alone.
  current (green): within ~2 days of expected.
  lagging Nd (amber): behind, but a past closed period still has data.
  empty (red): a PAST closed period with zero rows.
  awaiting (gray): today/future/after last-entered, or summary-grain site (VN/WB).
Never render red/error for today, future, or after a plant's last-entered date.

Data source: read the persisted manifest (plant, location, file_id,
file_title, modified_time, rows_found, date_range_in_data, columns_seen,
aggregates) + the Tier-1 completeness output already computed. If the manifest
isn't persisted, persist it on each refresh and read from it.

Design: reuse existing banner, metric cards, status pills, tables. Brand navy
#1F3864 + terracotta #C55A11, DD-MM-YYYY, sentence case, no emoji, mobile-first
(tables stack under 640px, cards wrap 2x2). Link /data in the main nav.
```

---

## 6. Notes

- This page also makes the "unaccounted files" advisory (drive_actual − expected) a natural
  fit: if Claude's advisory pass flags a workbook in the Drive that isn't in scope, surface it
  here as a dismissible "found, not tracked" row — gated to a human, never auto-added.
- Keep it read-only. No edit/refresh-source actions beyond the existing global "Refresh data".
