# AIFinanceHelper

[中文](README.md) · [Releases](https://github.com/Jeffspdf/AI-finance-assistant/releases/latest)

A local Windows application for student spending analysis, budgets, and explainable savings planning. Tkinter is the delivered desktop UI; Streamlit remains a web prototype. Optional compatible Chat Completions providers suggest categories and parse bounded questions. All money calculations run in deterministic Python code.

## Run

Use Windows x64 and Python 3.12 with Tk. Create a virtual environment, install `requirements.txt`, then run `python desktop.py`. End users can install the EXE from Releases without Python. Builds are unsigned. Quit before upgrading. Application data remains in `%LOCALAPPDATA%\ShiguangLedger` for backward compatibility and is retained by uninstall.

The desktop supports CSV/XLSX import, conflict-aware deduplication, audited edits, a 200-row paginated ledger, monthly/category budgets and source records, spending charts, record-based three-month planning, saved goals and actual contribution progress. Demo and personal databases are separate. The campus companion can be paused.

## Cross-source reconciliation (0.8.0)

M1a is accepted at v0.8.5 for the tested import formats, manual decisions, shared projection, category domains, explicit maintenance and legacy/snapshot visibility. The existing learned-rule manager already lists and deletes old rules; invalid income/expense rules are ignored during import. Refund netting remains a later M2 milestone. See [ninth-review assessment and closeout](docs/ROUND9_RESPONSE.md).

Version 0.8.5 implements review F1–F4: read-only discovery of legacy category conflicts across all source months, exact-ID navigation and explicit correction; kind-filtered web manual editing; snapshot identity checks across confirmation and before deletion; and clear scan/write/manual-confirmation boundaries. The file checks reduce races, without claiming atomic protection against a hostile local process. See [assessment and evidence](docs/ROUND8_RESPONSE.md).

Version 0.8.4 evaluates the seventh review before implementing E1–E3: disjoint income/expense/transfer categories, explicit selected-group repair, and repair-history/snapshot management. Legacy income/expense category conflicts are preserved until an explicit valid edit. Read-only dialogs do not scan; writable dialogs still scan on entry. Snapshots can be previewed and individually deleted after confirmation, with no automatic pruning. Development smoke tests launch the known build directly; releases remain unsigned. See [assessment and evidence](docs/ROUND7_RESPONSE.md).

Version 0.8.3 implements review D1–D4: transfer kind and transfer category must agree across all write paths, with linked editor controls. Legacy conflicts are backed up, category-normalized and marked for review without changing kind or money. Queries are read-only; invalid groups fail closed in the projection and are persistently repaired on startup or explicit writes. Zero-wallet groups require a second confirmation. C-level details name missing evidence and offer account/binding and rescan actions. See [sixth-round implementation and evidence](docs/ROUND6_RESPONSE.md).

Version 0.8.2 fixes Alipay A-level channel recognition, rejects multiple wallets in one purchase group, and invalidates affected decisions on category changes. Legacy invalid confirmations are repaired consistently with caches/audit. C/stale proposals cannot confirm directly. Imports auto-detect supported formats, show post-save reconciliation actions, and reject repeated source rows under changed account/parser declarations. Demo initialization is recorded once even after deletion. See [round-five scope and validation](docs/ROUND5_RESPONSE.md).

Version 0.8.1 adds Generate demo statements inside the demo workspace: choose start month, 1–12 months, 1–500 expenses per month, amount range, monthly income, optional transfers/refunds, and append or explicitly confirmed month replacement. Generation is local and atomic, keeps source batches, refreshes the selected month, and cannot target the personal ledger. [Usage and validation](docs/DEMO_GENERATOR.md).

Create accounts and dated funding declarations in Settings, then select an account (or explicitly Unspecified) and format when importing. WeChat CSV and a bounded bank-template-v1 XLSX are supported alongside generic import. All A/B/C candidates require manual confirmation. The wallet representative counts once; funding legs remain in the source ledger. Split restores counts; reruns preserve user decisions, and explicit reopening is available.

Settings provides a separate synthetic six-row demo: expense 120 -> confirm 60 -> split 120 yuan, with a pending 10-yuan refund and a 500-yuan transfer excluded from income/expense. Pending refunds are not deducted: these are not final net-expense figures. Overview, budget, QA, insights, planning estimates and the web prototype share the same projection.

Schema 4 uses pre-migration SQLite backups, atomic upgrades, a future-version guard, and previewed restoration with another pre-restore snapshot. Legacy IDs/categories are preserved, without fabricating original rows. File hashes reuse documents; every import has a batch; soft deletion preserves raw evidence/audit and is not physical erasure. Remote model activation/testing now requires a Key; loopback services can remain keyless. See [scope and implementation](docs/ROUND4_IMPLEMENTATION.md).

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

Limitations: RMB only, imported records may be incomplete, refunds need manual reconciliation, no-ID identical transactions may merge. Contributions are manually recorded and do not create ledger transactions. Imports parse in a background worker; selective row exclusion and manual column mapping are not implemented. No bank link, investment recommendations, cloud sync, push reminders, OCR, clean-VM certification. Final course report/slides/video and real team contribution statements are outside this software-fix iteration. Licensing is not assigned without the owner's choice.

## Round-two improvements (0.7)

Local, evidence-linked insights (changes, unusual spending and conditional goal estimates); quick entry Ctrl+N; command/search Ctrl+K; sortable right-aligned ledger; multi-row category changes with one-step session undo; opt-in exact-merchant/kind learning; previous-budget copy and reviewed historical-mean suggestions. A session-only assistant retains up to 20 messages per space, supports limited follow-ups and clarification, and confirms exact budget commands before writing. Async import previews before commit, exports complete error CSVs and discards cancelled parse results. In-flight parser code may finish in its worker; committing cannot be cancelled.

No UI dependency was added. Version 0.7.1 corrects the DPI regression: positive point fonts and 96-DPI layout units, a 1200x800 logical default and 1100x720 logical minimum capped to the desktop work area. The main page has one vertical viewport. No live model configuration was found, so model-quality evaluation remains unperformed. Subscription detection and an always-available chat drawer remain deferred. Run `python scripts/verify_round2_ui.py` for repeatable UI checks.

## DPI correction (0.7.1)

Central layout scaling covers windows, dialogs, chart coordinates, meters, tree rows, padding, wrapping and companion images. Body text is at least 10pt; row height is 44 logical pixels and sidebar width is 220. Summary amounts precede responsive 2/3-column insights. Edit/quick-entry forms scroll while their save buttons remain outside the viewport.

`python scripts/verify_dpi_ui.py` checks six pages at default/minimum size and nine dialogs/pet surfaces at simulated 100/125/150/200%, plus the actual native system DPI. Native 150% on the local 2560x1440 display produced an 1800x1200 window and 27px body line height. Other OS scale settings, mixed-DPI monitor switching and clean-VM installation remain unverified. Metrics and screenshots are in docs/screenshots/dpi.

## Update routes (0.7.4)

Update checks use fresh system network settings and then a direct official-GitHub route if needed. Invalid API payloads fall back too. Transfers can restart on the alternate route; SHA256 and official-origin constraints remain. Current-version, unknown-version and older-release states are distinct. Local bounded update diagnostics contain no keys or ledger data. The actual packaged EXE supports --diagnose-updates --download-latest for the same network implementation used by the GUI.
