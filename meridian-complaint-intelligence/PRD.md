# Product Requirements Document — Meridian Complaint Intelligence

**Status:** Initial implementation scope  
**Owner:** Meridian Insights Data Engineering  
**Source brief:** CFPB Consumer Complaint Database case study

## 1. Problem

Meridian analysts manually filter spreadsheets, read large volumes of consumer narratives, calculate trends, and write findings. The process is slow, inconsistent across analysts, and difficult to audit. Build a runnable assistant that accepts plain-English questions about CFPB consumer complaints and produces grounded, reproducible answers.

The assistant must combine structured analysis (counts, rankings, trends, rates) with narrative evidence (what consumers describe), while applying hard constraints such as company, product, state, and dates.

## 2. Goals

1. Build a reproducible snapshot pipeline for CFPB complaints received from **2024-01-01 through 2025-12-31** with consumer narratives.
2. Maintain visible `bronze/`, `silver/`, and `gold/` layers, each rebuildable from its predecessor.
3. Answer numeric questions using actual read-only queries against gold data and return the executed query.
4. Answer narrative questions using metadata-filtered retrieval and cite supporting CFPB Complaint IDs.
5. Support blended questions that produce both a calculated result and cited narrative explanation.
6. Explicitly abstain when the snapshot or schema cannot support a claim.
7. Generate `answers.json` for all supplied questions, through the system rather than manual authorship.

## 3. Non-goals for the initial version

- A production cloud data lake, multi-user service, or real-time daily ingestion.
- Embedding the full ~2M narrative corpus without metadata narrowing.
- Autonomous execution of arbitrary SQL.
- Inferring facts unavailable in the CFPB schema, including verified relief dollar amounts.

## 4. Users and representative questions

**Primary user:** a Meridian analyst preparing client, research, or regulator-facing findings.

Questions include:

- “What are people saying about debt collection?”
- “What are the most common problems people reported with Cash App during 2025?”
- “Which company had the biggest Q3 2025 spike, and what was driving it?”
- “Who is slowest to respond?”

The assistant must understand consumer brands that differ from legal entity names, e.g. Cash App → Block, Inc.; Zelle → Early Warning Services, LLC; TransUnion → TRANSUNION INTERMEDIATE HOLDINGS, INC.

## 5. Functional requirements

### 5.1 Snapshot and medallion pipeline

| Layer | Required contents | Acceptance criteria |
|---|---|---|
| `bronze/` | Raw fetched extract and immutable manifest | Manifest records source URL/endpoint, method, retrieval timestamp, date/narrative filters, count, file checksum, and schema/source version where available. |
| `silver/` | Canonical complaint records | Standardized names and columns; parsed dates; typed timely-response flag; deliberate null handling; documented company alias mapping; one trusted row per Complaint ID. |
| `gold/` | Query-ready data and retrieval artifacts | At minimum a company × product × month complaint-volume and timely-response table; aggregates reconcile to silver. |

Near-duplicate narratives may be collapsed only for retrieval diversity. Analytical counts remain one count per Complaint ID.

### 5.2 Structured-analysis tool

- Accept a typed analysis request: measure, grouping, filters, comparison/ranking, and limit.
- Compile it to parameterized, schema-allowlisted, read-only SQL against gold tables.
- Execute it with DuckDB (or an equivalent local analytical store).
- Return rows, the exact executed SQL, and query metadata.
- Reject unsupported requests and non-read-only operations.

### 5.3 Narrative-retrieval tool

- Apply extracted hard metadata filters before semantic retrieval.
- Retrieve a diverse, reproducible set using hybrid lexical and semantic relevance where feasible.
- Return Complaint ID, metadata, excerpt, and retrieval scores.
- Preserve explicit legal-name/brand resolution evidence.

### 5.4 Agentic orchestration

Use LangGraph/LangChain to coordinate tool use. The LLM may decide the evidence plan and next approved tool, but it cannot directly establish data truth.

Required tool flow:

1. check coverage and required field capability;
2. resolve company/brand entities when needed;
3. invoke structured analysis for quantitative claims;
4. invoke narrative retrieval for experience claims;
5. synthesize only from tool outputs;
6. validate citations, numerical claims, executed SQL, and abstentions.

Loops must be bounded. Persist a tool trace for every answer.

### 5.5 Answer contract

Each generated answer must conform to:

```json
{
  "question_id": "q07",
  "answer": "...",
  "citations": [14396683],
  "query": "SELECT ..." ,
  "abstained": false
}
```

- Narrative statements require cited Complaint IDs.
- Quantitative answers require the query that generated their figures.
- `abstained` is true with empty citations when grounding is unavailable.
- The answer must state material interpretation choices and source limitations.

## 6. Data constraints and mandatory abstentions

- The snapshot has no 2023 records; questions about March 2023 must abstain.
- “Closed with monetary relief” is a response category, not a verified dollar amount paid. The system must not answer how much consumers “typically got back.”
- If a requested company, product, state, or date range is absent, say so rather than broaden silently.

## 7. Architecture

```text
Question
  → LangGraph agent planner
  → coverage + entity tools
  → structured-analysis tool and/or metadata-filtered narrative retrieval
  → grounded synthesis
  → deterministic answer validator
  → answer record + tool trace
```

The implementation begins as a mock graph, then replaces mock tool bodies with pipeline-backed implementations. LangGraph controls orchestration; deterministic tools control data access and validation.

## 8. Quality and evaluation

Before delivery, report:

1. **Data quality:** Complaint ID uniqueness, date/type/null summaries, alias-map checks, and gold-to-silver reconciliation.
2. **NL-to-query correctness:** hand-authored test questions with expected results, including aliases, rates, rankings, and date boundaries.
3. **Retrieval quality:** a small judged set with supporting-evidence-in-top-k / Recall@k and filter-correctness checks.
4. **Faithfulness:** manual audit of answer claims against SQL rows and cited excerpts.
5. **Abstention behavior:** tests for missing date coverage, unsupported relief amounts, and absent slices.

## 9. Delivery artifacts

- `README.md`: <30-minute quick start and from-scratch rebuild instructions.
- `PRD.md`, decision write-up, and architecture diagram.
- Pipeline, LangGraph assistant, tests, and `answers.json`.
- Committed lightweight application artifacts where practical; raw multi-GB extract/indexes excluded from git.
- Documented production follow-up: incremental/idempotent daily refresh, data-quality monitoring, schema drift detection, observability, and access controls.

## 10. Initial milestones

1. Scaffold uv project, Git repository, agent instructions, PRD, and mock graph.
2. Profile local CSV and establish bronze manifest; implement silver canonicalization.
3. Build gold aggregates plus reconciliation tests.
4. Replace mock SQL and retrieval tools with real local implementations.
5. Add agent validation, evaluate all 15 questions, package the demo.
