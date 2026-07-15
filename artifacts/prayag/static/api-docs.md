# Prayag Production Analytics — API Reference

**Version:** v1  
**Base path:** `/data-api/v1`  
**Protocol:** HTTPS (GET requests only — read-only API)

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
| `utilisation` | `number \| null` | Run hours ÷ ideal hours (null for output-only plants) |
| `output_efficiency` | `number \| null` | Actual output ÷ ideal output |
| `mc_efficiency` | `number \| null` | Actual run hours ÷ Report-5 col-M ideal month hours |
| `rejection_pct` | `number \| null` | Rejection ÷ total output |
| `total_count` | `number` | Total output (unit is per-plant — do not sum across plants) |
| `actual_hours` | `number` | Run hours logged |
| `ideal_hours` | `number` | Planned hours (denominator for utilisation) |
| `output_by_unit` | `object` | Output broken out by unit (kg / Ltr / pcs) |
| `oee_available` | `bool` | False when OEE cannot be computed |
| `util_available` | `bool` | False for output-only plants (e.g. TANK) |
| `eff_available` | `bool` | False when no ideal-output baseline |

> **Important:** Never sum `total_count` across plants — units differ (MOULDING = kg, TANK = Ltr). Use `output_by_unit` for cross-plant aggregation.  
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
```

---

*All data is read-only and recomputed deterministically from the production Google Sheets on every request. No figures are stored or cached across API calls in a way that could serve stale data.*
