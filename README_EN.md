# AIFinanceHelper

[中文](README.md) · [Releases](https://github.com/Jeffspdf/AI-finance-assistant/releases/latest)

A local Windows application for student spending analysis, budgets, and explainable savings planning. Tkinter is the delivered desktop UI; Streamlit remains a web prototype. Optional compatible Chat Completions providers suggest categories and parse bounded questions. All money calculations run in deterministic Python code.

## Run

Use Windows x64 and Python 3.12 with Tk. Create a virtual environment, install `requirements.txt`, then run `python desktop.py`. End users can install the EXE from Releases without Python. Builds are unsigned. Quit before upgrading. Application data remains in `%LOCALAPPDATA%\ShiguangLedger` for backward compatibility and is retained by uninstall.

The desktop supports CSV/XLSX import, conflict-aware deduplication, audited edits, a 200-row paginated ledger, monthly/category budgets and source records, spending charts, record-based three-month planning, saved goals and actual contribution progress. Demo and personal databases are separate. The campus companion can be paused.

## Optional AI

Settings → model configuration. Enter the provider Base URL, model ID and Key; explicitly enable and consent. With Remember enabled, keys are protected by Windows current-user DPAPI and activation is restored after restart/update. Unchecking Remember keeps activation/key in this session only. Clear key removes the stored credential and disables the model. Old session-only keys must be entered once again; startup restoration makes no model request. Questions alone are sent for query interpretation. Classification separately confirms sending merchant/description/kind for at most 20 unresolved rows. Select suggestions to accept; rules/manual labels are protected. Local questions bypass the provider. Unsupported or invalid conditions are refused; models never execute SQL or calculate money.

## Verify

```text
python -m pytest -q
python scripts/reproduce_acceptance.py
python scripts/evaluate_ai.py
python scripts/verify_review_ui.py
```

See [validation](docs/VALIDATION.md), [architecture](docs/ARCHITECTURE.md), [AI evaluation](docs/AI_EVALUATION.md), [review response](docs/REVIEW_RESPONSE.md) and [AI use record](docs/AI_USAGE.md). Evaluation defaults to a mock oracle supplied with expected intents: this is an integration regression, not real AI accuracy. Use `--live` with local FINANCE_LLM_* variables to evaluate a real provider; no live test has been claimed.

Limitations: RMB only, imported records may be incomplete, refunds need manual reconciliation, no-ID identical transactions may merge. Contributions are manually recorded and do not create ledger transactions. Imports parse in a background worker; selective row exclusion and manual column mapping are not implemented. No bank link, investment recommendations, cloud sync, push reminders, OCR, one-click backup restore or clean-VM certification. Final course report/slides/video and real team contribution statements are outside this software-fix iteration. Licensing is not assigned without the owner's choice.

## Round-two improvements (0.7)

Local, evidence-linked insights (changes, unusual spending and conditional goal estimates); quick entry Ctrl+N; command/search Ctrl+K; sortable right-aligned ledger; multi-row category changes with one-step session undo; opt-in exact-merchant/kind learning; previous-budget copy and reviewed historical-mean suggestions. A session-only assistant retains up to 20 messages per space, supports limited follow-ups and clarification, and confirms exact budget commands before writing. Async import previews before commit, exports complete error CSVs and discards cancelled parse results. In-flight parser code may finish in its worker; committing cannot be cancelled.

No UI dependency was added. Version 0.7.1 corrects the DPI regression: positive point fonts and 96-DPI layout units, a 1200x800 logical default and 1100x720 logical minimum capped to the desktop work area. The main page has one vertical viewport. No live model configuration was found, so model-quality evaluation remains unperformed. Subscription detection and an always-available chat drawer remain deferred. Run `python scripts/verify_round2_ui.py` for repeatable UI checks.

## DPI correction (0.7.1)

Central layout scaling covers windows, dialogs, chart coordinates, meters, tree rows, padding, wrapping and companion images. Body text is at least 10pt; row height is 44 logical pixels and sidebar width is 220. Summary amounts precede responsive 2/3-column insights. Edit/quick-entry forms scroll while their save buttons remain outside the viewport.

`python scripts/verify_dpi_ui.py` checks six pages at default/minimum size and nine dialogs/pet surfaces at simulated 100/125/150/200%, plus the actual native system DPI. Native 150% on the local 2560x1440 display produced an 1800x1200 window and 27px body line height. Other OS scale settings, mixed-DPI monitor switching and clean-VM installation remain unverified. Metrics and screenshots are in docs/screenshots/dpi.
