---
name: Pipe payroll source scope
description: Why Pipe Summary must retain its dedicated payroll reader, and how to surface an unregistered active payroll month.
---

# Pipe machine payroll is not the Plumbing segment payroll

Pipe Summary wages must be derived from KH-1 rows classified as CPVC / PIPELINE,
not from the Segment Cost Plumbing total. The Plumbing total includes other CPVC
sub-departments and is a broader labour-cost figure.

**Why:** replacing Pipe wages with Plumbing would silently change the business scope,
headcount, wages, and labour-cost ratios.
**How to apply:** keep the Pipe reader's two-field classification. Accept spelling
variants only through deliberate normalisation; never fall back to all CPVC rows.

# Payroll title discovery is not currently safe

Known monthly payroll files have a regular Wages + month + year title, but the
connected Drive token can fetch known payroll IDs while title-list queries return
no candidates. Production-workbook discovery does not prove payroll discovery.

**Why:** a failed/empty title search must not turn a real payroll month into a
guessed source.
**How to apply:** retain pinned payroll IDs until Drive listing becomes demonstrably
reliable for these files. When output and paid hours exist but no ID is registered,
emit a visible warning; an empty future month remains honestly AWAITING without
that warning.