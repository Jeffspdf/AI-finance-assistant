# 2026-11-01 review response

Scope confirmed by user: software fixes and engineering documentation first. Final English report, final slide deck and demo video are outside this iteration. The external report is review evidence, not authority to delete a working copy or change licensing.

| Tasks | Resolution |
|---|---|
| T-01 / T-02 | Desktop budget page, persisted per-month/category budgets, expenditure evidence, home usage bars and dismissible overrun alerts. Goal planned/actual gaps shown. Fresh demo selects latest month with data. |
| T-03 | Last three complete months estimate with missing-month disclosure, per-month totals, A=current average expense and B=90% assumption. |
| T-04 | Additive contribution table, actual deposits/history/delete, actual vs expected progress. No duplicate ledger entries. Forecast completion date under variable actual saving is not claimed. |
| T-05 | Desktop AI classification, max 20 unresolved rows, separate send confirmation, accept selected / reject by closing, conditional save protects manual/rule labels. |
| T-06 / T-07 | Local-first structured query validation; merchant/date ranges/two-category comparison/three-month average; evidence, formula and limitations. Invalid model results do not broaden explicit filters. |
| T-08 | 30 authored questions + executable rules/mock/live evaluator. Default run is explicitly mock-oracle, not model accuracy. Live evaluation remains unperformed without credentials. |
| T-12 / T-13 | AI use record, Mermaid architecture, synthetic desktop screenshots. No invented team contributions. |
| T-14 / T-15 / T-17 | Fixed-date acceptance replay, datasets/results, Windows push/PR test CI, bilingual README and current evidence links. Remote CI run status is recorded only if actually observed. |
| T-16 / T-25 | New build/release version and official SHA256 comparison. Release verification is separate from a local test pass. |
| T-18 | Product branding unified; old private directory/AppId retained deliberately for compatibility. |
| T-20 / T-21 / T-22 / T-26 | 200-row ledger pagination, batch insertion with conflict semantics, explicit SQLite close, ASCII PowerShell error message. |
| T-09 / T-10 / T-11 | Deferred by user's explicit scope choice: final video, slides, report. |
| T-19 | License decision / GitHub topics deferred; no arbitrary license assigned. |
| T-23 / T-24 / T-27 | High-DPI real-machine QA, backup/restore workflow, manual mapping/selective import/cancelable parsing remain future work. Existing sample formats are bounded, not arbitrary-bank compatible. |

Two factual qualifications: the deterministic September food fixture totals RMB 145 across **six**, not four, source records; a time estimate such as "50,000 rows <=10s" is not a guaranteed acceptance result without a measured target environment.

The desktop development folder and git publication checkout are retained, not destructively consolidated. The publication checkout is the repository of record; only an explicit source allowlist is synchronized into it. No venv, user databases, credentials or build directories are published. Consolidation into one working copy is a future maintenance step, not a condition for functionality.

Open limitations: no clean-VM verification, no real paid model benchmark, no signed EXE, imports can block the UI for large files, no goal withdrawal tracking, no cloud/bank integration. Source-origin and fake-model tests do not establish real-world accuracy.

Later v0.7 follow-up: asynchronous parsing/cancelled-result discard and error CSV export supersede the import-blocking limitation above. Manual column mapping, row-level exclusion, restore workflow and actual high-DPI machine QA remain deferred. See ROUND2_RESPONSE.md for the current product scope.
