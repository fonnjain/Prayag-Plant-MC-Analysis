# Replit Agent Prompt — Deliver Management Reports as SEPARATE Excel files (one workbook per report)

> Use this together with the earlier master prompt (`Prayag_Replit_Management_Reports_Prompt.md`), which holds the recompute rules, column mappings, reconciliation logic, and Drive IDs. This prompt changes only the **packaging/delivery** and confirms the **full report set incl. PTMT**. The recompute layer is unchanged.

---

## 0. What changes

Today the Management Reports page produces ONE workbook with many worksheets. Change it so **each report is generated and downloaded as its own standalone `.xlsx` file**. Add:
- a **per-report "Download .xlsx"** button on the page, and
- a single **"Download all (ZIP)"** that bundles every report file for the selected plant + month.

Do **not** merge reports into a single multi-sheet workbook anymore. One report = one file. (A report may still contain a few worksheets *internally* if that report needs them — e.g. (D) with a summary tab + per-material detail tabs — but each **report** is its own workbook.)

**Unchanged and still non-negotiable:** every number is recomputed from the daily source workbooks; final/summary sheets are for format + validation only; AI writes narrative only; parse by header text; zero formula errors (run the recalc step on every generated file).

---

## 1. The full report set — each one its own file (incl. PTMT)

| # | Report (file) | Plant | Daily source it is recomputed from |
|---|---|---|---|
| 1 | (A) Pipe M/C Summary | KH | Report-5 + Report-11, date-wise max (incl. the pipe type-split as an internal tab) |
| 2 | (B) Moulding M/C Summary | KH | Report-12 (authoritative) |
| 3 | (C) Group of Moulding M/C Summary | KH | Report-12, grouped by mould tonnage |
| 4 | (D) Pipe Moulds Summary | KH | Report-12 mould/item-wise (validate vs 17–20; **17–20 tabs may be a stale snapshot — don't trust them**) |
| 5 | Garden Pipe M/C Summary | KH | Garden monthly workbook, MACHINE tabs |
| 6 | HDPE M/C Summary | KH | HDPE monthly workbook, MACHINE tabs |
| 7 | Moulding %age in Efficiency | KH | Report-12 (output/hr, utilisation) |
| 8 | Compound Compilation | KH | Reports 6/7/8 (**owner-skipped — build path, keep toggled off**) |
| 9 | Segment Wise Labour/Solar/Power Cost | KH | Employee-cost workbooks + manual power/solar inputs |
| 10 | TANK (KH) Ltr. Summary | KH | Tank-KH workbook DATA tab |
| 11 | TANK (VN) Ltr. Summary | VN | Tank-VN workbook DATA tab |
| 12 | TANK (WB) Ltr. Summary | WB | Tank-WB workbook DATA tab |
| 13 | **PTMT Moulds Summary** | **PTMT** | **PTMT monthly workbook → Report-5** (machine-wise: run/ideal hours, utilisation %, output kg) |
| 14 | **PTMT Moulding %age in Efficiency** | **PTMT** | **PTMT monthly workbook → Report-5**, grouped by mould tonnage |

Any report whose source month is empty/absent still produces a file, but the file states **"awaiting source"** rather than fabricating zeros.

### PTMT specifics (separate plant — must be included)
- PTMT lives in its own Drive folder; discover the month's `"PTMT Date Sheet & Monthly Report - <Month> '<YY>"` by `title contains`.
- Recompute from **Report-5** inside that workbook: per-machine `M/C NO.` (col C), `TOTAL RUN HOUR` (col D), `IDEAL HOUR` (col E), `Utilisation %` (col F), `TOTAL` output kg (col G).
- **Split injection moulding from Corrugator + Blow moulding** and show them apart. The source's stored TOTAL row is inconsistent (its run-hours include corrugator/blow, its kg does not); the recomputed **injection subtotal must equal the stored moulding total**. Exclude grinders (material recovery).
- PTMT output is in **kg only** (Report-6 is a shift/manpower roster, not pcs) — don't invent pcs.

---

## 2. Per-file structure (consistent across all reports)

Each generated `.xlsx`:
1. Title row: `"<Report name> — <Month> <Year>"`, brand navy `#1F3864`.
2. Sub-title: plant, method one-liner, units (kg / INR-per-kg / litres).
3. The report table(s) with live Excel formulas for all totals/ratios/shares (not hardcoded).
4. A **provenance/validation footer**: source workbook(s) used, the reconciliation tie-out result, and any flags ("awaiting source", "coverage warning", "stale-tab excluded").
5. Styling: Arial, navy headers, terracotta `#C55A11` subtotals, `dd-mm-yyyy` dates, zeros shown as `-`.

Run the LibreOffice recalc on every file and assert **0 formula errors** before it is offered for download.

---

## 3. Backend architecture (report registry + individual + zip routes)

Build a **report registry** so adding/removing a report is one entry. Each entry maps an id to a generator that returns an in-memory workbook.

```python
# reports/registry.py
REPORTS = [
  # (id, label, plant, generator_fn)
  ("a_pipe_mc",      "(A) Pipe M/C Summary",            "KH",   gen_pipe_mc),
  ("b_moulding_mc",  "(B) Moulding M/C Summary",        "KH",   gen_moulding_mc),
  ("c_group_mould",  "(C) Group of Moulding",           "KH",   gen_group_moulding),
  ("d_pipe_moulds",  "(D) Pipe Moulds Summary",         "KH",   gen_pipe_moulds),
  ("garden",         "Garden Pipe M/C Summary",         "KH",   gen_garden),
  ("hdpe",           "HDPE M/C Summary",                "KH",   gen_hdpe),
  ("mould_eff",      "Moulding %age in Efficiency",     "KH",   gen_moulding_eff),
  ("compound",       "Compound Compilation",            "KH",   gen_compound),      # toggled off
  ("segment_cost",   "Segment Labour/Solar/Power",      "KH",   gen_segment_cost),
  ("tank_kh",        "TANK (KH) Ltr. Summary",          "KH",   gen_tank_kh),
  ("tank_vn",        "TANK (VN) Ltr. Summary",          "VN",   gen_tank_vn),
  ("tank_wb",        "TANK (WB) Ltr. Summary",          "WB",   gen_tank_wb),
  ("ptmt_moulds",    "PTMT Moulds Summary",             "PTMT", gen_ptmt_moulds),
  ("ptmt_eff",       "PTMT %age in Efficiency",         "PTMT", gen_ptmt_eff),
]
```

```python
# each generator recomputes from source and returns an openpyxl Workbook
def gen_ptmt_moulds(plant, fy, month) -> Workbook: ...

def workbook_bytes(wb) -> bytes:
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()   # recalc/validate before this
```

**Routes (Flask):**
```python
@app.get("/reports/<rid>.xlsx")
def one_report(rid):
    fy, month = request.args["fy"], request.args["month"]
    wb = build_and_validate(rid, fy, month)          # runs recalc, asserts 0 errors
    return send_file(io.BytesIO(workbook_bytes(wb)),
        download_name=filename(rid, fy, month),
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/reports/all.zip")
def all_reports_zip():
    fy, month = request.args["fy"], request.args["month"]
    plant = request.args.get("plant")               # optional filter
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for rid, label, rplant, _ in REPORTS:
            if plant and rplant != plant: continue
            wb = build_and_validate(rid, fy, month)
            z.writestr(filename(rid, fy, month), workbook_bytes(wb))
    mem.seek(0)
    return send_file(mem, download_name=f"Prayag_Management_Reports_{month}-{fy}.zip",
                     as_attachment=True, mimetype="application/zip")
```

**File-name convention** (`filename(rid, fy, month)`):
`Prayag_<PLANT>_<slug>_<Mon-YYYY>.xlsx` — e.g.
`Prayag_KH_A_Pipe_MC_Summary_May-2026.xlsx`, `Prayag_KH_D_Pipe_Moulds_Summary_May-2026.xlsx`, `Prayag_PTMT_Moulds_Summary_May-2026.xlsx`, `Prayag_PTMT_Efficiency_May-2026.xlsx`.

**UI:** for the selected plant + month, render the report list from the registry — each row shows the report name, its headline number, and a **Download .xlsx** button hitting `/reports/<id>.xlsx`. Above the list, a **Download all (ZIP)** button hitting `/reports/all.zip`. Group rows by plant (KH / VN / WB / PTMT). Show the "awaiting source" state inline for any report whose month has no data.

---

## 4. Self-check numbers (generators must reproduce)

- (A) Pipe May'26: 313,637 kg / 30,484 kg rej · Apr'26: 157,883 / 13,030.
- (B) Moulding May'26: 75,771 kg; Jun'26: 89,100 kg.
- (D) May'26: mould/item-wise = 75,771 (17–20 tabs were stale — recompute from Report-12).
- Garden May'26: 53,234 kg; HDPE May'26: 1,369 kg.
- **PTMT May'26:** injection 101,580 kg @ 55.2% util (+ corrugator/blow 10,412) → grand 111,992.
- **PTMT Jun'26:** injection 141,097 kg @ 72.8% util (+ corrugator/blow 15,880) → grand 156,977.

A generated file that misses its target is a build failure, not a rounding issue — trace the header mapping and the reconciliation before shipping it.
