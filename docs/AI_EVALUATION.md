# AI evaluation

Reference date: 2026-10-09. Dataset: 30 authored questions and synthetic transactions.

Mode: **MOCK ORACLE ONLY. No real model was evaluated. Oracle reads the expected intent directly; its scores measure code plumbing, NOT intelligence or accuracy.**

| Mode | Exact intent / 30 | Answer consistency / 30 | Evidence integrity / 30 | Correct refusals / 10 | Fallbacks | Mean seconds |
|---|---:|---:|---:|---:|---:|---:|
| mock-oracle | 30 | 30 | 30 | 10 | 6 | 0.0007 |
| rules | 19 | 15 | 30 | 10 | 0 | 0.0003 |

Definitions: exact intent compares validated fields to the authored intent; answer consistency compares to fixture-intent execution of the same deterministic calculator; evidence integrity checks every returned ID/amount against imported data (vacuous for refusals); refusal checks 10 unsupported questions. Fallback counts failed/invalid model parsing. Latency includes query and calculation, plus network in live mode.

Limitations: small non-random dataset, synthetic transactions, same calculation code in reference. No real-world generalization claim, no classification accuracy claim. Finance math is tested separately.

Reproduce: `python scripts/evaluate_ai.py`. For live testing set FINANCE_LLM_BASE_URL, FINANCE_LLM_MODEL, FINANCE_LLM_API_KEY locally, then use `--live` (questions only are transmitted; may incur API fees). No keys are saved in the results.
