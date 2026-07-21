# Evaluation and acceptance checks

This repository evaluates the system at four boundaries. The tests run without a
live embedding or chat-model call; they use deterministic fixtures so results are
repeatable.

## 1. Data quality and reconciliation

| Check | Evidence | Acceptance condition |
|---|---|---|
| Bronze population | `tests/test_bronze.py` | date/narrative/Complaint-ID violations fail by default |
| Silver canonicalization | `tests/test_silver.py` | one trimmed Complaint ID, typed fields, documented duplicate policy |
| Gold reconciliation | `tests/test_gold.py` | every materialized metric table sums to dated Silver rows |
| Atomic publication | `tests/test_publication.py` | malformed/current-path/collision cases fail without replacing a published generation |

For the previously generated pinned snapshot, the recorded result was **2,036,539**
valid records, distinct Complaint IDs, and narratives, covering 2024-01-01 through
2025-12-31. Re-run the medallion commands to generate a fresh auditable manifest and
quality report for the copy of the input supplied to a reviewer.

## 2. NL-to-query correctness

`tests/test_analytics.py` checks typed-plan validation, table selection, filters,
parameterization, date bounds, timely-response denominators, rankings, and
read-only SQL construction. `tests/test_planner.py` captures the supplied question
contracts for the deterministic/offline adapter. `tests/test_semantic_planner.py`
checks that LLM JSON is compiled only into supported typed contracts and rejects
unsafe operations.

These tests establish that a numeric answer originates from a concrete Gold query;
they do not prove that an LLM will always interpret an ambiguous question as a human
would. Production monitoring should sample semantic plans and compare them with
analyst-approved intent labels.

## 3. Retrieval quality and filter correctness

`tests/test_retrieval.py` checks generation manifests, required metadata schema,
hard metadata/date filtering, Complaint-ID preservation, and malformed index
failures. `tests/test_validation.py` verifies every returned retrieval item—not just
cited items—against executed hard filters. It also rejects citations/quotes absent
from returned evidence.

The working set is deliberately not a corpus-prevalence evaluation set. It is a
question-aware 15,000-record illustrative index. A production retrieval evaluation
should add a blinded judged query set, Recall@k / evidence-support@k, diversity, and
false-positive filter metrics for each material company/product/time slice.

## 4. Faithfulness and abstention

`tests/test_synthesis.py`, `tests/test_validation.py`, and `tests/test_assistant.py`
verify that rendered claims have valid structured rows, exact Complaint-ID citations,
verbatim excerpts, bounded citation count, allowed templates, and stated
limitations. `tests/test_coverage.py` verifies abstentions for out-of-range dates,
unsupported auto-insurance claims, and unverified relief dollar amounts.

The last local validation run before packaging was:

```text
143 passed
ruff format --check .  passed
ruff check .           passed
git diff --check       passed
PYTHONPATH=src python -m py_compile src/meridian_assistant/*.py  passed
```

## Limits

Unit/integration checks prove contracts and failure behavior, not universal model
quality. The strongest remaining production work is a human-reviewed semantic-plan
benchmark and a judged retrieval set sampled from the full canonical corpus.
