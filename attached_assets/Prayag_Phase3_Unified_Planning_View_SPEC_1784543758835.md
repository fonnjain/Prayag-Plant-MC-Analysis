# Phase 3 — Unified Per-Machine Planning View  (SPEC — review only, NOT sent to Replit)

**Status of the build (confirmed with the Agent):** codebase is at **end of Phase 2C**. Live planning domains: `/planning`, `/materials`, `/maintenance`, `/manpower`. **Phase 2D is NOT done** (`/yield`, `/toolroom`, mixer-feed, PTMT wastage parsers do not exist yet). 171 tests pass; headlines unchanged.

**Dependency:** Phase 3 is the capstone that joins every planning domain into one per-machine screen. It reads best with 2D in place (feed + tooling + yield complete the bottleneck picture). **Recommended order: land Phase 2D first, then Phase 3.** Phase 3 can ship without 2D, but the "feed ready?" and "tool ready?" signals will show as *unknown* rather than red/green.

---

## 1. Goal (the original ask, finally assembled)

One screen per machine that answers, in priority order: **What should this machine run next → can it actually run it now → if not, what's the single blocking constraint?** Everything already captured (demand, material, maintenance, manpower, feed, tooling, yield, capacity) is recomputed source data; Phase 3 **joins and ranks** it — it introduces **no new source parsing**, only a join + a readiness model + a view.

## 2. New route & page
- `GET /plan` — plant/period-aware planning board (grid of machine cards).
- `GET /plan/<machine>` — per-machine detail: the ranked run queue + the readiness checklist + the bottleneck call-out.
- Add to nav and `/sources`/`/manifest` as a derived (not ingested) view.

## 3. Data join (all on-demand; NEVER on `/`)
A new module `plan.py` with `build_plan(plant, month)` that calls the existing on-demand loaders and joins on the canonical keys **MACHINE** and **ITEM CODE** (reuse `narrative.match_codes` for fuzzy machine-name matches across tabs):

| Input | From (already built) | What it contributes |
|---|---|---|
| Machine roster + utilisation/idle | existing `get_daily_records` / `metrics` (PIPE Report-5, PTMT Report-5, MOULDING Report-12) | which machines exist, current load, idle capacity, M/C Efficiency |
| Capacity / rate | MASTER (PTMT) + Report-12 cavity/cycle (moulding); Report-5 ideal rate (PIPE/HDPE) | theoretical output/hr per machine → how long a job takes |
| What to make | `/planning` PlanRecord (Report-1 family) | net requirement per item, days-of-cover, item→machine via MASTER `MACHINE NAME` |
| Material ready? | `/materials` MaterialRecord (Report-2/3/4) | RM/BOP/packaging reorder flags; a job is blocked if any input has cover ≤ lead time |
| Machine serviceable? | `/maintenance` MaintenanceRecord (Report-16 / Report-8) | PM-due / AMC / spares → maintenance window collision |
| Staffed? | `/manpower` ManpowerRecord (Report-22 / Report-6) | required vs actual manpower for the machine's shift |
| Feed ready? *(needs 2D)* | `/compound` mixer logs (Report-5 A–D) | compound availability upstream of extrusion |
| Tool ready? *(needs 2D)* | `/toolroom` (Report-21) | mould serviced/free |
| Yield risk *(needs 2D)* | `/yield` (Report-15) | recent wastage % trend per type |

**Nothing new is parsed.** If a 2D domain is absent, its check returns `unknown` (grey), not a failure.

## 4. Per-machine model — two computed things

### 4a. Ranked run queue (what to make next)
For each machine, gather candidate items via the item→machine map (PTMT MASTER `MACHINE NAME`; PIPE/moulding via the item's historical machine in Report-11/12). Rank by **net requirement** (from `/planning`), tie-broken by **days-of-cover ascending** (most-urgent stock first). Show, per candidate: net requirement (pcs/kg), days-of-cover, and **estimated run time = required qty ÷ theoretical rate** (capacity join) so a planner sees how the queue fills the machine's idle hours.

### 4b. Readiness checklist + single bottleneck
A fixed set of gates, each Green / Red / Grey(unknown), **recomputed, never a stored flag**:

| Gate | Green when | Red when | Source |
|---|---|---|---|
| Material | all inputs cover > lead time | any input cover ≤ lead time | `/materials` |
| Machine health | no PM due, not under breakdown | PM overdue / open breakdown ticket | `/maintenance` (+ Report-11A) |
| Manpower | actual ≥ required for the shift | actual < required or none logged | `/manpower` |
| Feed *(2D)* | compound available for the type | mixer breakdown / no compound | `/compound` |
| Tooling *(2D)* | mould free & serviced | mould in toolroom / not ready | `/toolroom` |
| Capacity | idle hours available this period | machine fully loaded / idle=0 | utilisation |

**Bottleneck = the highest-priority Red gate** (order: Material → Tooling → Feed → Machine health → Manpower → Capacity). The card shows one line: *"Ready"* or *"Blocked: <gate> — <specific reason>"* (e.g. "Blocked: Material — Resin K-67 cover 2d < lead 5d"). This is the single most useful output of the whole project.

## 5. UI
- `/plan`: grid of machine cards (reuse `_macros.html` KPI-card style). Each card: machine, headline KPI (existing), **top run-queue item**, **readiness dot-row (6 gates)**, and the **bottleneck line**. Sort machines by "most idle capacity with a ready job" first (actionable), blocked machines grouped below with their bottleneck.
- `/plan/<machine>`: full ranked queue table + the 6-gate checklist with the specific failing value + a "why" provenance line per gate (which report/as-of date).
- **Colour honesty:** Grey for unknown (missing 2D domain or no data), never a fake Green. Weekly-snapshot data (Report-1/2/3/4) shows its as-of date.

## 6. Guardrails (unchanged from prior phases)
- Recompute everything; **no stored ratio/flag trusted**; parse-by-header already done upstream (Phase 3 parses nothing new).
- `build_plan` runs **only** on `/plan*` routes — never on `/`. Verify `/` load time unaffected.
- Reuse existing loaders/caches (L1/L2); do not re-read sheets Phase 2 already cached.
- Fixture pytest: a synthetic machine with one Red gate resolves the correct bottleneck; the run-queue ranks by net requirement then days-of-cover; a missing 2D domain yields Grey not Red; existing headlines unchanged.

## 7. Acceptance
- For a PTMT injection machine in June, `/plan/<machine>` shows a ranked queue (from Report-1 demand mapped via MASTER) and a 6-gate readiness row; if June material for one of its inputs has cover ≤ lead, the bottleneck line names that input.
- For a PIPE M/C, the run-queue estimates run-time from the item's rate; an idle machine (e.g. M/C-7/8 in June) with a ready job surfaces at the top as an actionable opportunity.
- `/` dashboard load time unchanged; full offline pytest green.

---

## Sequencing recommendation
1. **Phase 2D** (already specced and verified against source — `/yield`, `/compound` mixer, `/toolroom`, PTMT wastage). Small, additive, completes the input set.
2. **Phase 3** (this spec) — the join + readiness model + `/plan` board.
3. *(Optional, later)* the deferred UX backlog the reviewer noted: interactive pages + PDF/AI for all 14 management reports, and the labour columns — not planning-critical, do last.

**Nothing here has been sent to Replit.** On your go, the natural order is: finish 2D, confirm, then hand over this Phase 3 spec.
