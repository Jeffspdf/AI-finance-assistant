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

Tests do not establish genuine-model accuracy, arbitrary-bank compatibility, a 50k-row latency guarantee or clean Windows VM installation. The DPI evidence added below is limited to native 150% and explicit Tk simulations, not mixed-monitor certification. Backup/restore and enhanced import cancellation/mapping remain unimplemented. Build, remote publish and remote CI results must be independently observed; source tests alone do not establish their success.

## Build and CI observation

The v0.6.0 packaged EXE passed --self-test with isolated data. The first GitHub review test workflow completed successfully: https://github.com/Jeffspdf/AI-finance-assistant/actions/runs/41282130254 . This run covered the review implementation before the final extra legacy-database test; the local final suite includes that extra test (86 passed). A later evidence-only commit starts a new remote run whose status must be checked separately.


## Second-round validation — v0.7.0

Local complete regression: **89 passed**. `scripts/verify_round2_ui.py` passed at 1024x680, including stable sort/edit identity, multi-row undo, quick entry, session chat/clarification, budget command cancel/confirm, async import cancel-without-write and confirmed commit. Screenshots in this directory now show v0.7 synthetic test data and compact visuals. Windows DPI awareness is enabled, but actual 125%/150% OS scale settings are still not certified. No live model configuration was present; no live-model benchmark was executed. Previous sections record the earlier v0.6 validation chronologically.

## Third-round DPI correction — v0.7.1

Full regression: **94 passed**. Both UI workflow scripts passed again using logical 1100x720 sizing. `scripts/verify_dpi_ui.py` checks six pages at default/minimum widths with real local follow-ups, plus quick entry, transaction edit, goal contributions, answer evidence, model settings, command search, merchant rules, update dialog and desktop pet. It uses isolated synthetic data and makes no model calls. No horizontal page spill, clipped/shortened dialog buttons, taskbar overlap or callback errors were detected.

The local native system is 2560x1440, 150% (Windows144 DPI, Tk143.858 estimate). Native default client size1800x1200, 10pt line height27px, table row66px. Explicit Tk simulations at100/125/150/200% also passed, with a work-area-limited2400x1272 client at200%. The simulations do not change system display settings and are not separate real-machine OS-scale certifications. Metrics and screenshots: [third-round response](ROUND3_RESPONSE.md), screenshots/dpi/*.json and *.png. The root screenshots are refreshed by the workflow scripts; dpi/ contains labelled scale-specific evidence. Mixed-DPI monitor transitions remain untested.

The v0.7.1 installer and portable ZIP were built locally. The actual packaged EXE passed its isolated-data `--self-test`. `scripts/verify_packaged_dpi.py` separately launched that EXE with temporary synthetic data and observed a144-DPI native1800x1200 client through the Windows window API, then closed it normally (exit0). Evidence: screenshots/dpi/packaged_native_metrics.json and packaged_native_home.png. This confirms a local packaged run, not installation on a clean Windows VM.

## API configuration persistence — v0.7.2

Full regression: **106 passed** (94 prior tests plus12 settings-persistence cases). Includes actual Windows current-user DPAPI cross-process decrypt and damaged-blob rejection. Persistence tests also cover legacy/corrupt settings, no plaintext key, startup with model requests blocked, session-only and keyless activation, clear/disable, endpoint tampering, and encryption/atomic-write failures preserving old settings.

`scripts/verify_model_persistence_ui.py` passed seven fresh-process stages: GUI save, restored enabled/key, endpoint change clears input without rewriting old saved config, clear, cleared restart, session-only save, session-only restart. All model requests were blocked; only synthetic keys used. `scripts/verify_dpi_ui.py native` and `... 2` passed six pages at default/minimum size and nine dialogs/pet surfaces; new model form remains scrollable with an unclipped fixed action footer.

The actual v0.7.2 packaged EXE passed `scripts/verify_packaged_model_settings.py`: two fresh launches with synthetic DPAPI credentials in a separate data directory; self-test verifies runtime key/activation equals the independently loaded configuration, and the ciphertext file remains unchanged. No live model request or real credential was used. Installer/data paths and AppId remain unchanged; no clean-machine install or migration of previously unsaved in-memory credentials is claimed.

A1 design documents were added to version control in a separate commit before this fix. They describe later account/reconciliation/refund work and are not runtime features of v0.7.2.

