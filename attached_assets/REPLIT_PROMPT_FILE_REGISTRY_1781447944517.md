# Seed the Drive file registry (full Post-Apr-2026 catalog, with daily flags)

This prompt carries the complete catalog of Prayag Drive files modified on/after 1-Apr-2026 (153 entries) so Replit can seed a **file registry** and know exactly which files to ingest, which to poll daily, and which roll over monthly. Source: connected Drive of `DEEPAKJ@prayagindia.com`, re-checked 13-Jun-2026. Companion to `REPLIT_PROMPT_INGESTION_AND_DASHBOARD.md` and `prayag_mis_schema.sql`.

## What Replit should do with this

1. **Seed a `file_registry` table** from the JSON in §3 (one row per file).
2. **Daily polling set** = rows where `daily = true` (15 files). The ingestion job reads only these
   each run; the rest are reference/historical.
3. **Monthly-rolling files** (`monthly_rolling = true`) have a NEW file ID every month — do **not**
   pin their ID. Resolve the current-month file by title pattern each run (keyword + year, most
   recently modified match). The IDs below are the June-2026 instances, valid as defaults only.
4. **Skip folders** (`is_folder = true`, 11 rows) — they have no readable content.
5. Treat this registry as the source of truth for file→department→function mapping used by the
   dashboards.

Registry table:

```sql
CREATE TABLE IF NOT EXISTS file_registry (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_name       text NOT NULL,
    file_id         text,                      -- null for folders
    department      text,
    function_type   text,
    last_modified   date,
    daily           boolean NOT NULL DEFAULT false,
    monthly_rolling boolean NOT NULL DEFAULT false,
    is_folder       boolean NOT NULL DEFAULT false,
    title_pattern   text,                      -- set for monthly_rolling files
    active          boolean NOT NULL DEFAULT true,
    UNIQUE (file_id),
    UNIQUE (file_name, last_modified)
);
```

Monthly title patterns (for the `monthly_rolling` rows):

```
Daily Production PTMT {MON} ' {YYYY}      e.g. "Daily Production PTMT JUN ' 2026"
Daily Production SINK {MON} ' {YYYY}
Sale PL {MON} {YYYY}-{YY}                 e.g. "Sale PL MAY 2026-27"
MACHINE PLANING {MONTH} {YYYY}
PUR PL {Mon}-{YY}                         e.g. "PUR PL Apr-26"
```
`{MON}` appears inconsistently in the Drive (`JUN`, `JULY`, `MAY`); match case-insensitively on the
segment keyword + year and take the most recently modified hit.

## 1. Daily polling set (15 files — ingest these every run)

```json
[
  {"name": "Daily Production PTMT JUN ' 2026", "file_id": "170xrcWDdTMvTLSJyCw3yGBWxqOOSfZkesGWunqKr8Rw", "dept": "Production & Planning", "function": "Daily production (PTMT)", "last_modified": "2026-06-12", "daily": true, "monthly_rolling": true, "is_folder": false},
  {"name": "Dispatch & Pending Dispatch Report", "file_id": "1VofuvdwSANgGPa_0ogAsgS1JuFQ3mHTFWCk6zkgZfks", "dept": "Logistics & Dispatch", "function": "Logistics / Dispatch", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Payment update", "file_id": "18ghQaIwPFaQXv4TOH4nYHnS5PM_hn09rpZo1GYK05dE", "dept": "Finance & Accounts", "function": "Daily payment", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "rate list", "file_id": "1njO-srsS29qiE4t45-zr5njbB7R2Zb-oSnv2NL1ONY4", "dept": "Pricing & Price Lists", "function": "Price list", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Purchase Rate 2026-27", "file_id": "1pdocd4tIINIR5ktRAbDBS61HqaobTdM-4MQxCyaTH1w", "dept": "Procurement & Purchase", "function": "Purchase rate", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Pending order", "file_id": "1dmt6uHOdZSIT0wgNkSfuK8W8d0YO8STW51PVOAAFHvY", "dept": "Stock & Inventory", "function": "Stock / Aging", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Order Sheet 26-27", "file_id": "1HFBAtvbAskejVkjuO8zHoEsE-pBAFij2ERMKFEvt64A", "dept": "Sales", "function": "Order sheet", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "SALE SHEET 26-27", "file_id": "19LQGpkbZiecGaXdBvl48rPZT2LUz3sKekeKX5fHu7Ps", "dept": "Sales", "function": "Sale sheet", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Production Sheet", "file_id": "1FQQsgKFw5KlmQMCLyoZC_-9IykhFsIEt-wf5kc6nc9s", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Payment update", "file_id": "1n4GUR8TrfQKXst87j_4qTrz2o7IlstGIrqneTLtFDFw", "dept": "Finance & Accounts", "function": "Daily payment", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale PL MAY 2026-27", "file_id": "1DSA6jOGAPcK3bAjcIf7Z4taz8rapShggT8-TCVpntHY", "dept": "Sales", "function": "Sale P&L (monthly)", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": true, "is_folder": false},
  {"name": "Daily Production SINK JUN ' 2026", "file_id": "1Y0jZKxxiUV1kHiRmCXrwoxSBM2dB1Z1SPwO1SFa3QDA", "dept": "Production & Planning", "function": "Daily production (SINK)", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": true, "is_folder": false},
  {"name": "State Head Sale 2026-27", "file_id": "1QIpcfgOVCFjcCmgU_DXKn8h7Bfa8rm2q2wB2HneTvKs", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-06-10", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "PTMT ANUJ", "file_id": "1AGmksx4gn6w0Wb9EF__yAV5v89IyAfX_f75ouW2c7Yw", "dept": "Management & MIS", "function": "Master daily production log", "last_modified": "2026-06-10", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale Master 2026-27", "file_id": "1sytliQa_YdoC_ddB6FxqlbXGwlG-qXknmi4g0VNHJ6o", "dept": "Sales", "function": "Sale master", "last_modified": "2026-06-09", "daily": true, "monthly_rolling": false, "is_folder": false}
]
```

## 2. Full registry (153 files — seed `file_registry`)

```json
[
  {"name": "Daily Production PTMT JUN ' 2026", "file_id": "170xrcWDdTMvTLSJyCw3yGBWxqOOSfZkesGWunqKr8Rw", "dept": "Production & Planning", "function": "Daily production (PTMT)", "last_modified": "2026-06-12", "daily": true, "monthly_rolling": true, "is_folder": false},
  {"name": "Dispatch & Pending Dispatch Report", "file_id": "1VofuvdwSANgGPa_0ogAsgS1JuFQ3mHTFWCk6zkgZfks", "dept": "Logistics & Dispatch", "function": "Logistics / Dispatch", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Payment update", "file_id": "18ghQaIwPFaQXv4TOH4nYHnS5PM_hn09rpZo1GYK05dE", "dept": "Finance & Accounts", "function": "Daily payment", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "rate list", "file_id": "1njO-srsS29qiE4t45-zr5njbB7R2Zb-oSnv2NL1ONY4", "dept": "Pricing & Price Lists", "function": "Price list", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Purchase Rate 2026-27", "file_id": "1pdocd4tIINIR5ktRAbDBS61HqaobTdM-4MQxCyaTH1w", "dept": "Procurement & Purchase", "function": "Purchase rate", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Pending order", "file_id": "1dmt6uHOdZSIT0wgNkSfuK8W8d0YO8STW51PVOAAFHvY", "dept": "Stock & Inventory", "function": "Stock / Aging", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Order Sheet 26-27", "file_id": "1HFBAtvbAskejVkjuO8zHoEsE-pBAFij2ERMKFEvt64A", "dept": "Sales", "function": "Order sheet", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "SALE SHEET 26-27", "file_id": "19LQGpkbZiecGaXdBvl48rPZT2LUz3sKekeKX5fHu7Ps", "dept": "Sales", "function": "Sale sheet", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Production Sheet", "file_id": "1FQQsgKFw5KlmQMCLyoZC_-9IykhFsIEt-wf5kc6nc9s", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Payment update", "file_id": "1n4GUR8TrfQKXst87j_4qTrz2o7IlstGIrqneTLtFDFw", "dept": "Finance & Accounts", "function": "Daily payment", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale PL MAY 2026-27", "file_id": "1DSA6jOGAPcK3bAjcIf7Z4taz8rapShggT8-TCVpntHY", "dept": "Sales", "function": "Sale P&L (monthly)", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": true, "is_folder": false},
  {"name": "Daily Production SINK JUN ' 2026", "file_id": "1Y0jZKxxiUV1kHiRmCXrwoxSBM2dB1Z1SPwO1SFa3QDA", "dept": "Production & Planning", "function": "Daily production (SINK)", "last_modified": "2026-06-11", "daily": true, "monthly_rolling": true, "is_folder": false},
  {"name": "Complaint sheet 2024", "file_id": "19p0dp3nRPeVGAD7WzggUvf_9nhvwIH-3aiw4Ec3bgFQ", "dept": "After-Sales & Service", "function": "Complaint log", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Mayank Plumbing Sheet", "file_id": "1Ry8b7PktZYT8r8tPgG-eHtfLwSA1_xah3g4BwAz-zjk", "dept": "Management & MIS", "function": "Other", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Purchase Rate 2025-26", "file_id": "12TxJz3lbaUGJeOkCp_JiuYQYrqV83VP_IQyZ4P9dyUs", "dept": "Procurement & Purchase", "function": "Purchase rate", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PARTY O/S & PAYMENT 25-26", "file_id": "1por9tFeT4jeRFc16rRW_S4z3Hk00t6zvlr840246zpA", "dept": "Finance & Accounts", "function": "Receivables", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Sale 2024-25", "file_id": "1iL1su3xwe40eG0QJHVwat97rhlMmADzeKQVfMw6ZrE0", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "STATE HEAD WISE MAR 2022 ORDER", "file_id": "1DefvIgKjt7CghlvghmtdhB34MFCxOOjYgqnSD1an4dM", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PURCHASE MASTER 2024-25", "file_id": "1S7yX7TSqmWefskW7JTtKy9uA7zjvTQh40kiedSVWcgk", "dept": "Procurement & Purchase", "function": "Purchase master", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "HR DELHI DAILY MAIL", "file_id": "1skr73s0dpFm0wkNV381xgh5lZ2v7hKnGspXEUVNzSus", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "plumber Coustmer confermation Whatsapp template sheet 25 JAN 2023", "file_id": "1M3z8o7UfeO-FX-dRuszH3I7Og7-HpA4t68Mh5SpLGxA", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "COUSTMER Whatsapp template sheet 25 JAN 2023", "file_id": "1-NobYmaQMG_D4jQzTrrSeXZrREVniI4L8tAuXNR2CDM", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "COUSTMER plumber confermation Whatsapp template sheet 25 JAN 2023", "file_id": "1CfhhaA6nj0UXDcgG3cuysEsqQ6EA0ZZcHOClfrbSsEM", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-06-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Sale 2026-27", "file_id": "1QIpcfgOVCFjcCmgU_DXKn8h7Bfa8rm2q2wB2HneTvKs", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-06-10", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "PTMT ANUJ", "file_id": "1AGmksx4gn6w0Wb9EF__yAV5v89IyAfX_f75ouW2c7Yw", "dept": "Management & MIS", "function": "Master daily production log", "last_modified": "2026-06-10", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale 2023-24", "file_id": "1R-jNPuy6ofJgIOqykulT0FDkuYVvh_FTAR59H4c5FkI", "dept": "Sales", "function": "Sales report", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Suresh Nair 2023-24", "file_id": "1q0P3e69u2dILPrAsluyhlIa4U3MkT8oBs9NGgu79HbU", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Sale mail-2024", "file_id": "1JbxNmTPLrJyKPjbqspzRkhxaLZDLsNA4OsHGEqF4tuA", "dept": "Sales", "function": "Daily sale mail", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "COUSTMER WHATSAPP AFTER COMPLENT", "file_id": "1zy9aqrItxI9JKSo240jIAYEPee--ua33KzcJ8dc1vzk", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sandeep JI 2025-26", "file_id": "16WpEU9plSfk8xy3-JBlb5n7VBYnkrX1tz3XyHSaGUYM", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Logistic 2025-26", "file_id": "1NAVMtJHYII3DttX912WSe0U_tm_yyUErUDr4FdNfhbE", "dept": "Logistics & Dispatch", "function": "Logistics / Dispatch", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sandeep JI 2024-25", "file_id": "15Q_kQNzJDnm6QgoT-f19SZ3nEJsrflXbWGVbEBvIjF8", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "STATE HEAD WISE May 2026 ORDER", "file_id": "1FcpOMskdCwJswFuQeBT5LhHridH8IROKgGZPlCL_fws", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "sale & dispatch data", "file_id": "1ITliDjJEjSs7QAuKQ4_QQc47o_67evUn0vRvIKj9HLQ", "dept": "Logistics & Dispatch", "function": "Logistics / Dispatch", "last_modified": "2026-06-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale Master 2026-27", "file_id": "1sytliQa_YdoC_ddB6FxqlbXGwlG-qXknmi4g0VNHJ6o", "dept": "Sales", "function": "Sale master", "last_modified": "2026-06-09", "daily": true, "monthly_rolling": false, "is_folder": false},
  {"name": "NASIR HUSAIN 2025-26", "file_id": "19Zopakv1LCLdOeG22FkarPMczHoSiznujhdZDuQdYUY", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Nasir JI 2026-27", "file_id": "1OI6q1QYaoSfY7fC4QcwvYeGEV6q6CRX6hTlFaVFmqFM", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Anant Singh JI 2026-27", "file_id": "1G3z_gOk5JR8yFmcVCadFCgpltjY1y0pI4ZBmGwrF2pU", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Anant Singh JI 2025-26", "file_id": "1LfyTEOdPo7f-PbfrKPNhzE3ftYGHP1OWdV4g5kbVUAQ", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Pawan Kumar 2025-26", "file_id": "1e0zBKJf8JAhOIVATE4FWnTsfR8B_V016RHlcY1yyJP0", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Rizvi ji JI 2025-26", "file_id": "1h3fZWioP2jm9Le2vb7EUbOEFV82snPt216T3PQfuA2E", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Snadeep ji 2026-27", "file_id": "18zT-a0a8d7rDIy2UpJGWyrWivFGKkBwdyRocPzgg8EA", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Pawan JI 2026-27", "file_id": "1H7zfz2BQyy5n5SJAAKfm9S3VJw89LF8dcU9GVgk-J8Q", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Deepak Jhangra(14)", "file_id": "1SkG2VyBK2fDcUyaLnCtnh6TX21clQrFcoJnibbDdt7c", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-08", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Deepak Jhangra DailyReport 2026-27", "file_id": "1-IHW5kw7SAJrszB6uA2Q2NSI_4ENjw6_LS1B-YSaslg", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-06-08", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale & Pur PL Summary 2026-27", "file_id": "1OhGlFHTxJ4XQetMurLmi_fvJGmb3fVjhCyIQ_bZxtsU", "dept": "Finance & Accounts", "function": "Consolidated P&L", "last_modified": "2026-06-06", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "PUR PL Apr-26", "file_id": "12xStab6YgAvwdjOXgdok9viBUGfpnLAZJRfWlMcIAhA", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-06-06", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Jyoti Singh DailyReport 2026-27", "file_id": "19nwL-XZ_NqcixrzLHu2ee8uP44s7njODW21k1xIIZ7k", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "SULINDER PAL 2025-26", "file_id": "1YY_Z5BklLmR8VY9nPGpUkKyvvrH-qr0XGtSjC3LasDA", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "MACHINE PLANING JUNE 2026", "file_id": "15mh7lXXuNqpyIg36jK7uMbensI4QRQ-5ILEqsjSbRNU", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "RIZVI JI JI 2026-27", "file_id": "19mz3gsT70ai4T2YYT029gZBKfz-YewQjT14sI-wshzI", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "SULINDER PAL JI 2026-27", "file_id": "1zX1UEQAiAOsEafRx9WKchi2BGbsrxp5_k11TXAn_p6U", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "SANITARYWARE", "file_id": "1f3JeAz8hTAuIHN3LZx5fNRin1e7byA-FRzWhgS3Kuqw", "dept": "Product & Segment Data", "function": "Segment master", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "MI (DELHI)", "file_id": "1kl8DA69TP7MQJNi4up2b31aQgb9LQ9GyokUP7ZUTBRY", "dept": "Management & MIS", "function": "MIS summary", "last_modified": "2026-06-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "BIJJU 2026-27", "file_id": "1bq6XgCIt6_-tBW3m3c_quqz1Hrbg1xVA-0X8Ve-E4H8", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sunil Patel 2026-27", "file_id": "1nOEDqVQ0X1eYSbDhq-9x5yoBWTFJm9VpX9tjptDV4m8", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "LALAN 2026-27", "file_id": "1ZkddXZhhC3OpK4fUGiDf64LrlAKrXIUGW4NzgxaYaJo", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Data 2025-26", "file_id": "(folder / no id)", "dept": "Sales", "function": "Folder (state-head sales)", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "Sate Head 2026-27", "file_id": "(folder / no id)", "dept": "Sales", "function": "Folder (state-head sales)", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "Tamilnadu 2026-27", "file_id": "1kATkh-w4zebYIzlyoK0_GzPR1DD1n0ryEow4Ng26bH8", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "AP TELENGANA 2026-27", "file_id": "1km-8e4Jw3X_1BoKJDeSTps6h9j3_o7m0g1TdrQzkVA4", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "12. Mar' 26", "file_id": "(folder / no id)", "dept": "Production & Planning", "function": "Folder (production)", "last_modified": "2026-06-04", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "NALWA STREET 2500", "file_id": "1p6O5zZaAAHHj9QpAxAjWQYcbRp242I0ZXhf-zW1s3HQ", "dept": "Sales", "function": "Customer ledger", "last_modified": "2026-06-03", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PRAYAG PRAYAS", "file_id": "1rY2XiP3LpYWwy3IF3oqYB-Rs2keDgI6A4jlqfbBqO6k", "dept": "Marketing & Branding", "function": "CSR / initiative", "last_modified": "2026-06-02", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale Master 2023-24", "file_id": "1zvtL4AAOvT714VDWjAmME5jyb4ruQn1Aay9LO7A3-tw", "dept": "Sales", "function": "Sale master", "last_modified": "2026-06-02", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Rizvi ji JI 2024-25", "file_id": "16a4FDP-XFSjLypQ2cYtdqOwXE1-9bopdW53MeDkP_2I", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-06-01", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Task", "file_id": "1RdO0ow_6Dm2qyIMapanukkI_beOAbHdDKzBQAVTe9Hs", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-06-01", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Production PTMT Mar ' 2026", "file_id": "1wi9FQAHkJG5rksk9WravDJ6Rtuw_Ks3LNcTvQSylERU", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-05-31", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "water tank", "file_id": "1orSb-aAs_QBCn0Ovh_ZMrDoKknBd0NfXLRck62UFnOs", "dept": "Management & MIS", "function": "Other", "last_modified": "2026-05-30", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Prayag Employee Work Tracker Responses", "file_id": "1HpGAPoS67bO0LZGoBtjm5NxjK3X4tUzrwXvBkAO_MZ4", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-05-30", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Prayag Employee Work Responsibility Tracker", "file_id": "1Xy_aTyNWAkxAnVxjmlSqT69Uq3KyjQk_3bf3Of2JZPc", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-05-30", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Pending Order Ptmt Report 21/05/2026", "file_id": "1MZZGBYij577k8worhfjylHpk9uCBdyyb0cYTf3qiwO8", "dept": "Stock & Inventory", "function": "Stock / Aging", "last_modified": "2026-05-29", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Whatsapp template sheet 07-july-2024 COUSTEMR", "file_id": "1xa20Adr3jmLVrYo0z-MQGMcUvf8BwRvm3acvNlqJPHw", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-05-29", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Whatsapp template sheet 07-july-2024 STATE HEAD", "file_id": "1q0Pog8zthCfmjb5znfcn-kxGH3mIlbro9gvuIJGoyJE", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-05-29", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Logistic Whatsapp template sheet 07-july-2023", "file_id": "1gSh8YT5dkZfWOWwomFfxrpdaRWlY0l3_tbb5v5Dx6aw", "dept": "Logistics & Dispatch", "function": "Logistics / Dispatch", "last_modified": "2026-05-29", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "sheet index", "file_id": "1rEK27vzmsxEhXYp0VMG2tCgWyooI-lLRCzaucWqPQIc", "dept": "Management & MIS", "function": "Drive index", "last_modified": "2026-05-28", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Drive on INDEX 28/05/2026", "file_id": "1D2gy4Gi3O3YSuQZmguzi115I4B_zEjyipljIe7hocSI", "dept": "Management & MIS", "function": "Drive index", "last_modified": "2026-05-28", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Drive on INDEX", "file_id": "1une_VsKXEJvXFOpvkfK4CumLXE1xpZsiAfnkFbYUumI", "dept": "Management & MIS", "function": "Drive index", "last_modified": "2026-05-28", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Drive Sheet Index", "file_id": "1tbXd0ZtFzLHLw-OON6FB2IUtug-_mB3HxPjKzvyqx_Y", "dept": "Management & MIS", "function": "Drive index", "last_modified": "2026-05-28", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "GP MARGIN MONTH ON MONTH SUMMARY'", "file_id": "1Nsm_Y2xQaUWwo59Nz6qUks8JFHVyX7MgfCqLaYoUiSM", "dept": "Finance & Accounts", "function": "GP margin", "last_modified": "2026-05-28", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Kalra Sanitations and the million rupee loan", "file_id": "1ndCU0JiMAh7RSrNzDFR7vD7JeGR3CbVW", "dept": "Finance & Accounts", "function": "Account note (audio)", "last_modified": "2026-05-27", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PO 12600665 KALRA SANITATIONS - 5/27/26 5:42 PM.pdf", "file_id": "1LuYAm8cTnSuT-4PzLge94RJk4nHrklaJ", "dept": "Finance & Accounts", "function": "Invoice / Receipt", "last_modified": "2026-05-27", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PO 12600665 KALRA SANITATIONS.pdf", "file_id": "1hF04tqkifTCQ4hwzsooru0iYyvknpNPS", "dept": "Finance & Accounts", "function": "Invoice / Receipt", "last_modified": "2026-05-27", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sanjiv Jain Ceo it box", "file_id": "1v6BucpDzpICoMNSQ0ZydKJDb5NH5Wch8Tn8DN1HZO1U", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-05-27", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale PL APR 2026-27", "file_id": "16W-fxW6srpBsCtP6-xMR8FoLRhb3z9L_3ls5ZIfLmgc", "dept": "Sales", "function": "Sale P&L (monthly)", "last_modified": "2026-05-27", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Sale & Pur PL Summary 2025-26", "file_id": "13muZf9-tFNkXbR3zetL8ibY5GVK7tQRVHi-tofSHm5U", "dept": "Finance & Accounts", "function": "Consolidated P&L", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "PUR PL Feb-26", "file_id": "1zfbloXWBsqV3qOW8MGR8iVbRd4JjgSGGXKJURnxUkao", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Production Plan CP MAR' 2026", "file_id": "1zzeLdZeYuX0IcDMy6e87-eIham1-8T1lvsJQrTMqskE", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Key Person Deprtment Wise", "file_id": "1K5BRupur8iZGLqFKYs5WreJ_Lnvftc4R_o_Et5rtZzA", "dept": "HR & Admin", "function": "Org directory", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale PL Feb 2025-26", "file_id": "1O6HAWhX26K0PBMvXsddlnFBnSXD1KFEHQ19AEKvtq-U", "dept": "Sales", "function": "Sale P&L (monthly)", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Production Plan 25-26", "file_id": "(folder / no id)", "dept": "Production & Planning", "function": "Folder (production)", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "Drive on INDEX EA", "file_id": "1EkP8ItduG7P8ph1pCE93hMddunMFUUPQUVl7t_nv4Xs", "dept": "Management & MIS", "function": "Drive index", "last_modified": "2026-05-26", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Ptmt Retort", "file_id": "1KBeWVloje_KEMX9V0Vs_takDweps-_Sq12FOtaGSx8Q", "dept": "Management & MIS", "function": "Other", "last_modified": "2026-05-25", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "CP LIST", "file_id": "1JYceZXF_UOkoe1KGIiyb34aik6onf2RS0JSWCyrC7Jc", "dept": "Pricing & Price Lists", "function": "Price list", "last_modified": "2026-05-22", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "BABU 2024-25", "file_id": "15LczDfi40RcWoRkv8gca8DELcrlTngKl_zVlbROooHw", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-05-21", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PTMT MAY-26 PLAN VS ORDER VS PRODUCTION", "file_id": "1ONxsMd0rPHzRmdp9OJgyriFyifyK6aYjmNtEbaKUC2U", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-05-21", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "CP SALE REPORT", "file_id": "1TJkn-XtFbW9ckN776J_Dki60I8SVok1f8qWcC43EqIs", "dept": "Sales", "function": "Sales report", "last_modified": "2026-05-21", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "CP feb march-25", "file_id": "1_wD6XEOaGjIGxjg07QUftgf5JUpFmjgUi8alOAMpZig", "dept": "Sales", "function": "CP segment sales", "last_modified": "2026-05-21", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Pending Order Plumbing Report 21/05/2026", "file_id": "1-FJ4UGAIUrULQduh430ho4X0bdHwxStC9Xkkswo9R_Q", "dept": "Stock & Inventory", "function": "Stock / Aging", "last_modified": "2026-05-21", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "LALAN 2025-26", "file_id": "1pY5XyoA0qNhpOR4TLTQJxHh_3tu0BXFamR_6bcBFKxQ", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-05-20", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "LALAN 2024-25", "file_id": "1A4zZaDKJXaSwzJ0Kx_KieOz9SVX-57LgewttfvnQxoQ", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-05-20", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "S.Creditors [ 2025-26 ].zip", "file_id": "1h5MxEAhrSqC10kK0s9HTo5dZkyWDIkU4", "dept": "Finance & Accounts", "function": "Archive", "last_modified": "2026-05-19", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "DEEPAK", "file_id": "(folder / no id)", "dept": "Management & MIS", "function": "Folder (MDO office)", "last_modified": "2026-05-18", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "Production Plan", "file_id": "(folder / no id)", "dept": "Production & Planning", "function": "Folder (production)", "last_modified": "2026-05-17", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "_Sandeep JI 2025-26", "file_id": "1ftKxSG9TzpRTApk4a96Kn6WyLxcFSZTefGf-tsMhKr8", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "BIJJU 2025-26", "file_id": "10JjRNd779tIknM1az_dSpS-V7cFFuBy-Yd-3B3J_AEA", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Payment update", "file_id": "11-r2Pb4IVIfTfWVq06UKtcM5gH1H6hDaOT8V-Tur_Nc", "dept": "Finance & Accounts", "function": "Daily payment", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Wise Sale", "file_id": "1KgnZ5iCKszgJ_tACijCzDaPlAf1sHwFWgNSYazH6jGY", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Prayag_Polymers_Ltd_(Sandeep_Ji).pdf", "file_id": "1orYSlguR1nGFNrB2kQjYHucefPRFuNOy", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PARTY O_S & PAYMENT 26-27", "file_id": "1zjDlqrGIGxZmRMlMfMfBH4Yx7c5HU7ePCp01g2sr8DQ", "dept": "Finance & Accounts", "function": "Receivables", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "_Sale & Pur PL Summary 2025-26", "file_id": "1wsduIZ0T-zIL2nViVMFN3GQ9ZNzKuvvWqp-hRClmo1M", "dept": "Finance & Accounts", "function": "Consolidated P&L", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "HO", "file_id": "(folder / no id)", "dept": "Management & MIS", "function": "Folder (MDO office)", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "GP MARGIN 2024-25", "file_id": "1Fp5omFItcrpcw9MMobB4uBt9UUTY9gqB6DB-GvYi05E", "dept": "Finance & Accounts", "function": "GP margin", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale Comparison -24-25 _ 25-26", "file_id": "1NLbAcor4ty_yLA_GUijV1jBSAJSJB8wPO5WjhhY3hv4", "dept": "Sales", "function": "Sales report", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "MDO", "file_id": "(folder / no id)", "dept": "Management & MIS", "function": "Folder (MDO office)", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "6 YEAR SALE", "file_id": "1ilnmTpbOVUD9_G90ttz41XgVLQrUucMpUzzvxsRS-vc", "dept": "Sales", "function": "Sales report", "last_modified": "2026-05-16", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PARTY O/S & PAYMENT 26-27", "file_id": "1q7zZoLrUMO4vqzG8CtbKA68gMik1z7YjvuCksm1HV94", "dept": "Finance & Accounts", "function": "Receivables", "last_modified": "2026-05-12", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Dabit & Cradit Note", "file_id": "1a0vQOYx-xuinfUtklTubTb1bTTDHLb9-MGyRWmRjVYU", "dept": "Finance & Accounts", "function": "Debit / Credit notes", "last_modified": "2026-05-12", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "SALE PL 2026-27", "file_id": "(folder / no id)", "dept": "Finance & Accounts", "function": "Folder (P&L)", "last_modified": "2026-05-11", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "PL 2026-27", "file_id": "(folder / no id)", "dept": "Finance & Accounts", "function": "Folder (P&L)", "last_modified": "2026-05-11", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "PUR PL 2026-27", "file_id": "(folder / no id)", "dept": "Finance & Accounts", "function": "Folder (P&L)", "last_modified": "2026-05-11", "daily": false, "monthly_rolling": false, "is_folder": true},
  {"name": "NPD", "file_id": "1_MUcpKC25QvtsWHPg_5WRUd0BtFXH4LqCEFeSORW3Mw", "dept": "Product & Segment Data", "function": "New product dev", "last_modified": "2026-05-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale Master 2025-26", "file_id": "1HjzsyZDqeCYXCG-l7CX10ddxCgi1-C31xUO1YlHFMUU", "dept": "Sales", "function": "Sale master", "last_modified": "2026-05-07", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Shweta 06-05-2026", "file_id": "1U0SUF9BqDTSEacW0Wm-BocODjpFP5nTfO68TcZimsc0", "dept": "HR & Admin", "function": "HR / staff tracking", "last_modified": "2026-05-06", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Sale Summary 2026-27", "file_id": "1qs2R4Nb1Za_BCPcWY6EzrC5ZsGtWJgRtZFJMFe4033w", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-05-05", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "FUND FLOW UPDATE MAR 2026", "file_id": "1AzEcv22DlWnQWOZKLJlZ9LyaKInrCWcxAcRs1y7fBNE", "dept": "Finance & Accounts", "function": "Fund flow", "last_modified": "2026-05-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sample", "file_id": "1y9Naxe238b2D7Wm5jbtHIFvnFe33WX2aFVYtABn5r9E", "dept": "Management & MIS", "function": "Working file", "last_modified": "2026-05-04", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Sale Summary 2025-26", "file_id": "1ctm5oiWWJTbYtrTmlg4Y5l9J87b3PIsSbyPH0hbJCg8", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-05-03", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PUR PL APR-25", "file_id": "1IzFLHZbWyGtKfNxCwyGpbfCA045tzEUADeVe3Rn-mbs", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-05-01", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Pawan Kumar 2024-25", "file_id": "1ce4qX13i0eDqen-Cgv_B_cDxes1A56Mj0ggPZMG19y8", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-30", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "NASIR 2024-25", "file_id": "1fo6hDODHObipTl1FMX2LBnIVJQf7sV7vzGZrsnSbKMw", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-30", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "State Head Sale 2025-26", "file_id": "1RuXHIXfusOT-VDdDqeuB-Nx-pxyVkmrJsqr21BB-NUA", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-04-24", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "TAMILNADU & ANDMAN 2025-26", "file_id": "1smr0qQiEumF_LITBj5IEPGzpXJqvq0826acDvFtMpl0", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-20", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "BIJJU 2024-25", "file_id": "1Gm5C8s0RnzJr_hthov95YTELJcM6PL_pij-tRXYWJ1E", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-20", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "ROD REPORT", "file_id": "1fgT5KwitTmOYcENnt35ilLzLymexnT7Ugqja_1UgfUY", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-04-20", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "COUSTMER Whatsapp Sheet", "file_id": "1iIb-6jqGIgIeUyFxCmYOiPCsviah8Iy2zv5eRtbkusM", "dept": "CRM & Customer Engagement", "function": "CRM / WhatsApp", "last_modified": "2026-04-18", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "LIC PAYMENT DETAIL", "file_id": "16-i8hdtFiQXP-_dzfFw0EHQnU78udrsOMYUdRkGrNWI", "dept": "Finance & Accounts", "function": "LIC payments", "last_modified": "2026-04-18", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "1st Qtr Pur Rate 23-24 and 22-23", "file_id": "1ngPjfbYM8Kv80BdjSjoN-I4-uJgBxIrHzxJaMnO89tw", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-04-14", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Daily Production PLUMBING Mar ' 2026", "file_id": "1lchel5xWqO7EN2a0myuqupbcNA1XLtss5Q5hG3XR-aI", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-04-13", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "SUNIL PATEL 2025-26", "file_id": "1RmQk1J3ReAkABZ4iojTXN2lOEUOAX17yrqh9nV1stmM", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-13", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Summary Report Dated 13-Apr-26", "file_id": "18TS0VYpGUhN53tY6DWt40mdUcDQEh9pXy68eoYdQjuY", "dept": "Management & MIS", "function": "MIS summary", "last_modified": "2026-04-13", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "GARDEN PIPE", "file_id": "1MKqpaUStMuPHGx-F5QHnvAefPNpWhBIWmhSudI7r6Fk", "dept": "Product & Segment Data", "function": "Segment data", "last_modified": "2026-04-11", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PUR PL Jan-26", "file_id": "1YgJCpDCYRnLMGr2ebRwzcHlKhJI-CNNGD3qkcy5LOa0", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-04-10", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Suresh Nair 2025-26", "file_id": "1WOf6j5V9nLWPA2OxfK7VgWG-oa-PQMiKSrZxmM0kxuk", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-10", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PUR PL Oct-25", "file_id": "1EKzKd65cZEm-_X9TvSyNnzCBFq3GvHP18l9hSQDRFGk", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-04-10", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "KAVITA SALE DATA", "file_id": "1DZ0VUTZ6uIOdsGshJbO6BDIAl9EmAF_eZVWRzWhT98I", "dept": "Sales", "function": "Sales report", "last_modified": "2026-04-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PUR PL Mar-26", "file_id": "16XfqaEPAeT0qw205sh5UzpcTtBNRYfxwnN3gDIOPCCY", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-04-09", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Sale PL Mar 2025-26", "file_id": "1wfnql0n9Sr4zDzvKFPNOw4yZwptWdcV3aFLSd0ea4sw", "dept": "Sales", "function": "Sale P&L (monthly)", "last_modified": "2026-04-09", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "PTMT SFG REPORT", "file_id": "1Gs2W3_bWsS4BMfSLyyTF3XiWLUNv9LWF8Kf0UfTRrfk", "dept": "Production & Planning", "function": "Production plan/consumption", "last_modified": "2026-04-09", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "PUR PL Mar-26", "file_id": "1xKf_CLpkIy6dF4hGCWptfJHzKmG6m3g8Me7-xl7Yb3Y", "dept": "Procurement & Purchase", "function": "Purchase P&L", "last_modified": "2026-04-09", "daily": false, "monthly_rolling": true, "is_folder": false},
  {"name": "Copy of LALAN 2025-26", "file_id": "1BNcpVP9oL3OdZJo5gCScpp7aXQFus2ege2JdRcrxmg0", "dept": "Sales", "function": "State-head / distributor sheet", "last_modified": "2026-04-06", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Copy of State Head Sale 2025-26", "file_id": "1ld0ka6mS-8vCCzmhT8wNEaFzpgrP3Yw4FSUIRKcF1_g", "dept": "Sales", "function": "State-head sales", "last_modified": "2026-04-06", "daily": false, "monthly_rolling": false, "is_folder": false},
  {"name": "Sale PL Feb 2025-26", "file_id": "1ktMXNYm5oCJYOW0mMGwemie8sjOF-h8KaGCHWp2Ht0I", "dept": "Sales", "function": "Sale P&L (monthly)", "last_modified": "2026-04-02", "daily": false, "monthly_rolling": true, "is_folder": false}
]
```

## 3. Acceptance criteria
- `file_registry` is seeded with all 153 rows; `UNIQUE(file_id)` prevents duplicate registration.
- The daily job ingests exactly the 15 `daily = true` files (folders and reference files excluded).
- On month rollover, `monthly_rolling` files resolve to the new month's file by title pattern with no
  code/ID change; the registry row's `file_id` is updated to the resolved ID.
- Folders (`is_folder = true`) are never sent to the parser.
