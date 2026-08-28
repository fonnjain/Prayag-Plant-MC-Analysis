# Prayag Production Analytics — API Reference

**Version:** v1  
**Base path:** `/data-api/v1`  
**Protocol:** HTTPS. Read-only data endpoints use `GET`; planning previews use non-persistent `POST` requests.

---

## Authentication

Every data endpoint requires an API key. Generate one from the dashboard at **Settings → API Keys**.

Send the key using any of these methods (header is preferred):

| Method | Example |
|--------|---------|
| Header | `X-API-Key: prayag-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| Bearer token | `Authorization: Bearer prayag-xxxx…` |
| Query param | `?api_key=prayag-xxxx…` |

Multiple keys can be active at the same time — any valid key authorises a request.

### Error responses

| Status | `error` field | Meaning |
|--------|--------------|---------|
| `401` | `unauthorized` | Missing or invalid API key |
| `503` | `api_disabled` | No key has been configured on this deployment |
| `502` | `source_unavailable` | The production Google Sheets could not be read |
| `400` | `invalid_schedule_request` | The schedule-preview body is incomplete or invalid |
| `422` | `no_schedulable_demand` | Every submitted schedule line is data-limited; the response includes per-item coverage reasons |
| `503` | `planning_data_unavailable` | The required Plumbing planning master could not be read |
| `409` | `schedule_machine_pool_overlap` | A machine is registered in both pipe and fitting pools, so separate previews would over-commit it; fix the machine-pool configuration before retrying |
| `400` | `invalid_corrective_replan_request` | The corrective-preview body is incomplete or invalid |
| `503` | `corrective_actuals_unavailable` | Report-11 and Report-12 could not both be read for the requested month |
| `500` | `corrective_replan_failed` | The validated request could not be computed; internal exception details are not returned |

---

## Endpoints

### `GET /data-api/v1/health`
Liveness check. **No authentication required.**

**Response**
```json
{
  "ok": true,
  "api_enabled": true
}
```

---

### `GET /data-api/v1/plants`
Returns every plant's code, name, location, and the months for which daily data is wired.

**Response**
```json
{
  "plants": [
    {
      "code": "PIPE",
      "name": "Pipe",
      "location": "Kaharani",
      "daily_months": ["2026-04", "2026-05", "2026-06"],
      "daily_wired": true
    }
  ]
}
```

---

### `GET /data-api/v1/periods`
Returns valid period tokens and the list of calendar months that have production data.

**Response**
```json
{
  "period_tokens": ["last_updated", "yesterday", "last_week", "last_month",
                    "current_fy", "prior_fy", "q1", "q2", "q3", "q4", "custom",
                    "YYYY-MM", "YYYY-MM-DD", "1..12"],
  "months_with_data": ["2026-04", "2026-05", "2026-06"]
}
```

---

### `GET /data-api/v1/summary`
Computed headline metrics for a period. Same figures the dashboard displays — recomputed from raw sheets, never fabricated.

**Query parameters**

| Parameter | Example | Description |
|-----------|---------|-------------|
| `period` | `current_fy` | Period token or exact date (see tokens below) |
| `plant` | `PIPE` | Filter to one plant (enables `by_machine` and `by_segment`) |
| `segment` | `UPVC` | Further filter by product segment |
| `machine` | `PIPE M/C-1` | Filter to a single machine |
| `from_date` | `2026-04-01` | Custom range start (use with `period=custom`) |
| `to_date` | `2026-06-30` | Custom range end |

**Period tokens**

| Token | Meaning |
|-------|---------|
| `last_updated` | Each plant's own most recent day with real data |
| `yesterday` | Previous calendar day |
| `last_week` | Last 7 days |
| `last_month` | Previous calendar month |
| `current_fy` | Apr 2025 – Mar 2026 (current financial year) |
| `prior_fy` | Previous financial year |
| `q1` … `q4` | Fiscal quarters (Q1 = Apr–Jun) |
| `YYYY-MM` | Exact calendar month, e.g. `2026-06` |
| `YYYY-MM-DD` | Single day, e.g. `2026-06-15` |
| `1` … `12` | Fiscal-year month number (Apr = 1, Mar = 12) |

**Response**
```json
{
  "period": {
    "requested": "current_fy",
    "label": "FY 2025-26",
    "from": "2025-04-01",
    "to": "2026-03-31",
    "months": ["2025-04", "2025-05", "..."],
    "daily_first": true,
    "banner": null
  },
  "filters": {
    "plant": null,
    "segment": null,
    "machine": null
  },
  "figures_gated": false,
  "overall": { ... },
  "by_plant": {
    "PIPE": { ... },
    "MOULDING": { ... }
  },
  "by_machine": { ... },
  "by_segment": { ... },
  "by_date": {
    "2026-06-01": { ... }
  },
  "confirmation": { ... },
  "quarantined_rows": 0,
  "row_count": 312
}
```

**`figures_gated`** — when `true` the period has unresolved data-confirmation errors. The dashboard shows "needs review" instead of figures; treat the payload as provisional.

**Metrics block** (appears in `overall`, `by_plant`, `by_machine`, `by_date`)

| Field | Type | Description |
|-------|------|-------------|
| `oee` | `number \| null` | OEE % (null when no baseline) |
| `availability` | `number \| null` | Availability % |
| `performance` | `number \| null` | Performance % |
| `quality` | `number \| null` | Quality % |
| `utilisation` | `number \| null` | Run hours ÷ ideal hours for the machines that reported in the requested period; this is reporting-machine capacity, not fixed-fleet capacity (null for output-only plants) |
| `output_efficiency` | `number \| null` | Output from rows that have an ideal-output baseline ÷ ideal output; output without an ideal baseline is excluded from the numerator |
| `mc_efficiency` | `number \| null` | Actual run hours ÷ Report-5 col-M ideal month hours |
| `rejection_pct` | `number \| null` | Rejection ÷ `total_count`; the denominator follows `total_count_basis` (for example, `net` for PIPE and `gross` for PTMT). Consumers must read `total_count_basis` rather than assume a denominator |
| `total_count` | `number` | Source output (unit is per-plant — do not sum across plants). Read `total_count_basis` before interpreting it. |
| `total_count_basis` | `"net" \| "gross" \| "mixed" \| "unknown"` | Whether `total_count` is source net/good output, source gross output before rejection, a mixed rollup, or not documented. PTMT is `gross`; its separate management headline uses `good_count`/net output. |
| `good_count` | `number` | Net/good output after compatible rejection has been deducted at the machine-month aggregate grain. |
| `actual_hours` | `number` | Run hours logged |
| `ideal_hours` | `number` | Planned hours (denominator for utilisation) |
| `output_by_unit` | `object` | Output broken out by unit (kg / Ltr / pcs) |
| `oee_available` | `bool` | False when OEE cannot be computed |
| `util_available` | `bool` | False for output-only plants (e.g. TANK) |
| `eff_available` | `bool` | False when no ideal-output baseline |

> **Important:** Never sum `total_count` across plants — units differ (MOULDING = kg, TANK = Ltr). Use `output_by_unit` for cross-plant aggregation.  
> **PTMT:** `total_count` is the source-gross production figure; `good_count` is the comparable net production figure used by the PTMT management headline. Both are exposed explicitly and neither is substituted for the other.
> A ratio without a real baseline is always `null`, never `0`.

**Confirmation block**

| Field | Type | Description |
|-------|------|-------------|
| `status` | `"ok" \| "warning" \| "error"` | Worst-severity issue across the period |
| `released` | `bool` | True when a manager sign-off has released an error gate |
| `signed_off` | `bool` | True when any sign-off exists |
| `signoff.by` | `string` | Approver name |
| `signoff.at` | `string` | Sign-off timestamp (dd-mm-yyyy HH:MM) |
| `fingerprint` | `string` | Content hash of the source data at sign-off time |
| `issues` | `array` | Individual confirmation issues (see below) |

---

### `GET /data-api/v1/records`
Raw row-level data with full provenance. Same filters as `/summary`.

**Response**
```json
{
  "period": { "requested": "...", "label": "...", "from": "...", "to": "..." },
  "figures_gated": false,
  "confirmation_status": "ok",
  "row_count": 312,
  "rows": [
    {
      "plant": "PIPE",
      "machine": "PIPE M/C-1",
      "date": "2026-06-01",
      "total_count": 850,
      "reject_count": 42,
      "actual_hours": 7.5,
      "ideal_hours": 22,
      "ideal_month_hours": 500,
      "ideal_source": "derived",
      "runhours_tracked": true,
      "source_tab": "Report-5",
      "...": "..."
    }
  ],
  "quarantined": []
}
```

---

### `POST /data-api/v1/schedule`
Returns a **non-persistent**, capacity-feasible Plumbing schedule preview. Send
one request for either `pipe` or `fitting`; make a separate request when both
kinds are needed. The endpoint reads the current machine master, routing, BOM,
rates, rejection/wastage inputs, and recorded machine downtime. It does not
create a plan run, freeze demand, write plan lines, or change a saved calendar.

**Request**
```json
{
  "segment": "PLUMBING",
  "month": "2026-07",
  "kind": "pipe",
  "week_days": [7, 7, 7, 10],
  "demand": [
    {
      "item_code": "CPVC-EXAMPLE-20MM",
      "raw_code": "CPVC Example 20 mm",
      "material": "CPVC",
      "qty_pcs": 12000
    }
  ]
}
```

| Field | Required | Rules |
|-------|----------|-------|
| `segment` | Yes | Must be exactly `PLUMBING`. PTMT is not supported. |
| `month` | Yes | A real calendar month in `YYYY-MM` format. |
| `kind` | Yes | Either `pipe` or `fitting`. |
| `week_days` | Yes | Exactly four positive whole-day counts. Their sum cannot exceed the selected calendar month. Include Sundays here when the planning app intends to schedule them. |
| `demand` | Yes | A non-empty array of normalized demand lines. With no W1–W4 input, each line starts in week 1 and cascades through later capacity. |
| `demand[].item_code` | Yes | Normalized product code used to find the MP master data. |
| `demand[].raw_code` | No | Original display code. Defaults to `item_code`. |
| `demand[].material` | Yes | One of `CPVC`, `UPVC`, `SWR`, or `AGRI`. |
| `demand[].qty_pcs` | Yes | Positive requested piece quantity. |

Optional planning-app fields such as colour, category, urgency, and supplied
weight are ignored. **Do not use request weight as an override:** the schedule
always resolves its per-piece BOM weight from the current `mp_bom_weight` master.

**Response**

The native schedule fields remain at the top level: `blocks`, `weekly_fill`,
`unfinished`, capacity and idle totals, `week_days`, `kind`, and downtime totals.
`kind` echoes the requested `pipe` or `fitting` schedule type, so callers can
retain the source when showing both independent pools together. A locked machine
has `DOWN` blocks, making its excluded capacity explicit.

Three additional blocks make modelling coverage explicit:

- `coverage.items` returns one row for every submitted demand line, in request
  order. `status` is `schedulable`, `partial`, or `not_modellable`;
  `can_schedule` is the independent machine-allocation decision.
- `coverage.summary` aggregates item count, demand pieces, and percentage by
  status overall, by material, and by canonical category.
- `data_limited` contains only `can_schedule=false` lines. Mixed requests still
  schedule valid lines; if every line is data-limited the endpoint returns
  `422 no_schedulable_demand` with the same coverage blocks and no schedule.

`unfinished` is capacity/downtime-limited demand only. A missing BOM, route,
rate, or active machine is never inserted into `unfinished`.

Per-item classifications:

| `status` | Meaning |
|----------|---------|
| `schedulable` | Direct BOM, item/fitting-standard route, and direct usable rate are present |
| `partial` | BOM is present, but route/rate uses a documented fallback or has no usable fallback; check `can_schedule` |
| `not_modellable` | No BOM weight exists |

`route` identifies `direct`, `material_fallback`, `missing`, `inactive`, or
`not_evaluated` when a missing BOM prevents later modelling steps.
`rate` identifies `direct`, `cycle_fallback`, `material_fallback`,
`overall_fallback`, `estimated_average`, `estimated`, `missing`, or
`not_evaluated`. Stable machine-readable reason codes include `missing_bom`,
`missing_route`, `inactive_route`,
`missing_rate`, `route_fallback`, and `rate_fallback`.

`demand_reconciliation` deliberately keeps two different piece bases separate:

1. submitted net requested pieces = schedulable net requested pieces +
   data-limited net requested pieces;
2. modelled gross pieces after rejection uplift = scheduled gross pieces +
   capacity-limited gross pieces.

Do not add a net-request field to a gross-production field.
`unfinished[].remaining_pcs` is the remaining **gross** production-piece
quantity after rejection uplift; `remaining_kg` is the equivalent
engine-derived material quantity.

```json
{
  "segment": "PLUMBING",
  "kind": "pipe",
  "effective_month": "2026-07",
  "week_days": [7, 7, 7, 10],
  "blocks": [{"week": 1, "day": 1, "machine": "M/C-3", "shift": "DAY"}],
  "weekly_fill": [{"week": 1, "machine": "M/C-3", "capacity_hrs": 140.0}],
  "unfinished": [{
    "item_code": "CPVC-EXAMPLE-20MM",
    "remaining_hours": 18.0,
    "remaining_kg": 720.0,
    "remaining_pcs": 900.0
  }],
  "coverage": {
    "items": [{
      "item_code": "CPVCEXAMPLE20MM",
      "raw_code": "CPVC Example 20 mm",
      "kind": "pipe",
      "material": "CPVC",
      "category": "CPVC Pipe",
      "requested_pcs": 12000,
      "status": "partial",
      "can_schedule": true,
      "bom": "direct",
      "route": "direct",
      "rate": "material_fallback",
      "reasons": ["rate_fallback"]
    }],
    "summary": {
      "total_item_count": 1,
      "total_demand_pcs": 12000,
      "by_status": {
        "schedulable": {"item_count": 0, "demand_pcs": 0, "demand_pct": 0},
        "partial": {"item_count": 1, "demand_pcs": 12000, "demand_pct": 100},
        "not_modellable": {"item_count": 0, "demand_pcs": 0, "demand_pct": 0}
      }
    }
  },
  "data_limited": [],
  "demand_reconciliation": {
    "submitted_requested_pcs": 12000,
    "schedulable_requested_pcs": 12000,
    "data_limited_requested_pcs": 0,
    "modelled_gross_pcs": 12360,
    "scheduled_gross_pcs": 11460,
    "capacity_limited_gross_pcs": 900
  },
  "downtime_machine_days": 2,
  "downtime_hours_lost": 40.0
}
```

Pipe and fitting previews are independently mergeable only while their
machine-master pools are disjoint. Every request checks both current pools. If
any machine appears in both, the endpoint returns
`409 schedule_machine_pool_overlap` with the conflicting machine names instead
of producing schedules that could double-commit capacity. This is a
configuration conflict, not a transient availability failure, so callers
should not retry unchanged.

---

### `POST /data-api/v1/corrective-replan`
Returns a **non-persistent** Plumbing run-rate projection. This endpoint answers
where output will land if the production pace observed so far continues. It is
not a machine-capacity statement; use `/schedule` for what the configured
machines can physically make.

The API reads Report-11 and Report-12 internally from the monthly PIPE workbook.
Raw actual rows are never accepted from the caller. It does not create or
modify a plan run, freeze demand, write plan lines, or change a saved calendar.

**Request**
```json
{
  "segment": "PLUMBING",
  "month": "2026-07",
  "as_of_date": "2026-07-15",
  "week_days": [7, 7, 7, 10],
  "demand": [
    {
      "item_code": "CPVC-PIPE-20MM",
      "material": "CPVC",
      "category": "pipe",
      "qty_pcs": 12000,
      "produced_to_date_pcs": 3400
    },
    {
      "item_code": "UPVC-FITTING-EXAMPLE",
      "material": "UPVC",
      "category": "fitting",
      "qty_pcs": 8000,
      "produced_to_date_pcs": 2100
    }
  ],
  "cap_feasible_by_cat": {
    "CPVC Pipe": 9000,
    "UPVC Fitting": 6500
  }
}
```

| Field | Required | Rules |
|-------|----------|-------|
| `segment` | Yes | Must be exactly `PLUMBING`. PTMT is not supported. |
| `month` | Yes | A real calendar month in `YYYY-MM` format. |
| `as_of_date` | Yes | A real `YYYY-MM-DD` date inside `month`. |
| `week_days` | Yes | Exactly four positive whole-day counts. Their sum cannot exceed the month's calendar days. Include worked Sundays in these buckets. No legacy calendar fallback is used. |
| `demand` | Yes | A non-empty array. Multiple rows in the same material/category are summed by the corrective engine. |
| `demand[].item_code` | Yes | Normalized for contract consistency and traceability. Corrective category totals do not use BOM weight. |
| `demand[].material` | Yes | One of `CPVC`, `UPVC`, `SWR`, or `AGRI`. |
| `demand[].category` | Yes | One of `pipe`, `fitting`, or `solvent` (case-insensitive). |
| `demand[].qty_pcs` | Yes | Positive requested piece quantity. |
| `demand[].produced_to_date_pcs` | Yes | Non-negative piece quantity already produced against this demand line. It may exceed `qty_pcs`; remaining demand then floors at zero. |
| `cap_feasible_by_cat` | No | Object of non-negative piece totals keyed by canonical categories such as `CPVC Pipe`, `UPVC Fitting`, or `SWR Solvent`. Unknown category keys are rejected. |

`cap_feasible_by_cat` lets the planning app place the main run's machine answer
beside the corrective pace answer. It does not affect pace, projected output,
or shortfall calculations.

**Response**

The native corrective result fields are returned without renaming. Category
quantities are in **pieces**; `cap_per_day` is pieces/day. Explicit
`low_confidence` and `not_started` flags are added because a missing pace is not
the same as zero shortfall.

```json
{
  "month": "2026-07",
  "as_of_date": "2026-07-15",
  "week_days": [7, 7, 7, 10],
  "working_days_total": 31,
  "working_days_elapsed": 14,
  "working_days_remaining": 17,
  "min_days_for_p90": 5,
  "categories": [
    {
      "category": "CPVC Pipe",
      "n_days": 3,
      "produced_to_date": 3600,
      "cap_per_day": 1200,
      "method": "mean(low-confidence,3d)",
      "remaining": 8600,
      "working_days_remaining": 17,
      "feasible": 20400,
      "shortfall": 0,
      "shortfall_pct": 0,
      "cap_feasible": 9000,
      "low_confidence": true,
      "not_started": false
    }
  ],
  "warnings": [],
  "units": {
    "produced_to_date": "pcs",
    "remaining": "pcs",
    "cap_per_day": "pcs/day",
    "feasible": "pcs",
    "shortfall": "pcs",
    "cap_feasible": "pcs"
  }
}
```

Pace method:

- At least `min_days_for_p90` non-zero production days: `method` is `p90`.
- One to four non-zero production days: `method` is
  `mean(low-confidence,Nd)` and `low_confidence` is `true`.
- No observed production: `method` is `none` and `not_started` is `true`.

`remaining`, `feasible`, and `shortfall` are pieces. The schedule endpoint uses
different native units: `unfinished[].remaining_pcs` is pieces and
`unfinished[].remaining_kg` is the engine-derived kg equivalent.

---

## Design invariants

These guarantee the API always agrees with the dashboard:

1. **Daily-first figures.** Monthly and FY totals are summed from authoritative daily workbooks, not the monthly summary grid (the grid undercounts). A read failure shows an honest error — the lower grid total is never substituted.

2. **No fake 0%.** A ratio whose denominator is missing or zero is `null` in the API response, never `0`. Check the `*_available` flags to understand why.

3. **Data-confirmation gating.** When `figures_gated: true`, one or more error-tier confirmation checks failed and have not been released by a manager sign-off. Treat the figures as provisional — the same way the dashboard marks them "needs review".

4. **Per-plant units.** Output is reported in the plant's own unit (MOULDING = kg, TANK = Ltr). Use `output_by_unit` — never add `total_count` from different plants together.

5. **Sign-off binding.** A manager sign-off releases the error gate for a specific data fingerprint. If the underlying sheet changes after sign-off, the gate automatically re-engages and `figures_gated` returns to `true`.

---

## Example requests

```bash
# Check the API is alive
curl https://your-domain/data-api/v1/health

# Current FY headline — all plants
curl -H "X-API-Key: prayag-xxxx..." \
     https://your-domain/data-api/v1/summary?period=current_fy

# June 2026, PIPE plant only (adds by_machine + by_segment + by_date)
curl -H "X-API-Key: prayag-xxxx..." \
     "https://your-domain/data-api/v1/summary?period=2026-06&plant=PIPE"

# Raw rows for yesterday, all plants
curl -H "X-API-Key: prayag-xxxx..." \
     https://your-domain/data-api/v1/records?period=yesterday

# Custom date range
curl -H "X-API-Key: prayag-xxxx..." \
     "https://your-domain/data-api/v1/summary?period=custom&from_date=2026-04-01&to_date=2026-06-30"

# Non-persistent Plumbing pipe schedule preview
curl -X POST -H "X-API-Key: prayag-xxxx..." \
     -H "Content-Type: application/json" \
     https://your-domain/data-api/v1/schedule \
     -d '{"segment":"PLUMBING","month":"2026-07","kind":"pipe","week_days":[7,7,7,10],"demand":[{"item_code":"CPVC-EXAMPLE-20MM","material":"CPVC","qty_pcs":12000}]}'

# Non-persistent Plumbing corrective run-rate preview
curl -X POST -H "X-API-Key: prayag-xxxx..." \
     -H "Content-Type: application/json" \
     https://your-domain/data-api/v1/corrective-replan \
     -d '{"segment":"PLUMBING","month":"2026-07","as_of_date":"2026-07-15","week_days":[7,7,7,10],"demand":[{"item_code":"CPVC-EXAMPLE-20MM","material":"CPVC","category":"pipe","qty_pcs":12000,"produced_to_date_pcs":3400}],"cap_feasible_by_cat":{"CPVC Pipe":9000}}'
```

---

*Production-data endpoints are read-only and recomputed deterministically from the production Google Sheets. Plumbing schedule and corrective previews are also non-persistent: no preview is stored, frozen, or written back.*
