# Agent instructions

## Working agreement

- Use `uv` for Python environments, dependency changes, and commands. Do not use global `pip` or commit a `.venv`.
- Keep data layers explicit: `bronze/`, `silver/`, and `gold/` are rebuildable and separately auditable.
- Treat `data/complaints.csv` as source input. Never edit it in place. Do not commit raw CFPB data or generated indexes.
- Quantitative claims must come from executed, read-only queries and return the exact query.
- Narrative claims must be grounded in retrieved records and cite their Complaint IDs.
- Preserve source limitations and abstain rather than infer unsupported facts.
- Never put API keys or secrets in source control; use environment variables.

## Required workflow after every code change

1. Run the smallest relevant validation with `uv run` (and broader tests when warranted).
2. Inspect the diff yourself.
3. Request an **external, fresh-context subagent review** of the changed code before calling the work complete. The reviewer must be read-only and report evidence-backed findings with paths/lines.
4. Address blocking review findings, then re-run validation. Record any deferred non-blocking items in the final handoff.

## Code conventions

- Prefer small typed functions, explicit tool contracts, and Pydantic models at LLM/data boundaries.
- Keep LangGraph orchestration separate from tool implementations.
- Tool code must be independently testable without an LLM or network access.
- Do not give an LLM unrestricted SQL execution. Compile typed analysis plans to schema-allowlisted, read-only SQL.
- Bound agent tool loops and persist tool traces for auditability.
