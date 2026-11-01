# Validation record — 2026-11-01

## Completed locally

- Full regression: **86 passed**, Python 3.12 / Windows; includes original Streamlit UI tests. Run: `python -m pytest -q`. The first sandbox run stalled on local socket setup; the same suite passed in the normal local execution environment. This is not recorded as an application failure or a skipped test pass.
- Desktop self-test: passes in an isolated FINANCE_DESKTOP_DATA_DIR, including overview, ledger, budget, plan, assistant and settings pages. No personal ledger was modified.
- `python scripts/verify_review_ui.py`: verifies budget persistence, food expense evidence, three-month estimate, actual goal deposits, selected AI suggestion acceptance (mock), source protection and minimum-window controls. It uses temporary databases and synthetic screenshots.
- `python scripts/reproduce_acceptance.py`: 100 unique synthetic rows, repeated import skips 100, 4 invalid rows rejected, Excel parsing passes. September food expense RMB 145, budget RMB 100, overrun RMB 45, **6** source records. Last-three-complete-month average expense = 340333 cents, rounded to cents.
- New regression tests cover migration/contribution cascade, future-deposit rejection, manual category protection, merchant/date/compare/average queries, explicit filter omission, invalid model fields, SHA256 mismatch rejection and official checksum lookup.
- `python scripts/evaluate_ai.py`: 30 authored questions, 10 local + 10 model-only formulations + 10 refusal questions. Default is mock-oracle only. See AI_EVALUATION.md for exact metrics and limitations. No live model claim.

## Evidence

![Budget](screenshots/budget_page.png)

![Overview](screenshots/home.png)

![Planning](screenshots/plan.png)

The screenshots are generated from deterministic demo data, then UI test changes (e.g. one accepted mock category). They are not real finances or a live-model demonstration. The reference date of the acceptance dataset is October 9; the real test execution date is November 1.

## Limits

Tests do not establish genuine-model accuracy, arbitrary-bank compatibility, a 50k-row latency guarantee, clean Windows VM installation or high-DPI correctness. Backup/restore and enhanced import cancellation/mapping remain unimplemented. Build, remote publish and remote CI results must be independently observed; source tests alone do not establish their success.

## Build and CI observation

The v0.6.0 packaged EXE passed --self-test with isolated data. The first GitHub review test workflow completed successfully: https://github.com/Jeffspdf/AI-finance-assistant/actions/runs/41282130254 . This run covered the review implementation before the final extra legacy-database test; the local final suite includes that extra test (86 passed). A later evidence-only commit starts a new remote run whose status must be checked separately.


## Second-round validation — v0.7.0

Local complete regression: **89 passed**. `scripts/verify_round2_ui.py` passed at 1024x680, including stable sort/edit identity, multi-row undo, quick entry, session chat/clarification, budget command cancel/confirm, async import cancel-without-write and confirmed commit. Screenshots in this directory now show v0.7 synthetic test data and compact visuals. Windows DPI awareness is enabled, but actual 125%/150% OS scale settings are still not certified. No live model configuration was present; no live-model benchmark was executed. Previous sections record the earlier v0.6 validation chronologically.

