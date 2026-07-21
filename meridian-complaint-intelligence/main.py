"""Diagram/mock-only LangGraph demonstration (not the production answer path).

Use ``python -m meridian_assistant`` for production answer and batch commands.
This file deliberately uses mocked tools/data so we can agree on the agentic shape
before wiring bronze/silver/gold tables and a real retrieval index into it.

Install:
    uv sync

Run:
    export OPENAI_API_KEY=...
    uv run python main.py "Which company had the biggest spike in complaints in Q3 2025?"

The LLM chooses tools and their order. The graph only constrains it to approved,
auditable operations. Tool implementations are intentionally fake placeholders.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

# In production, this is a versioned silver.company_aliases table.
COMPANY_ALIASES = {
    "cash app": "Block, Inc.",
    "zelle": "EARLY WARNING SERVICES, LLC",
    "transunion": "TRANSUNION INTERMEDIATE HOLDINGS, INC.",
    "mohela": "MOHELA",
}


@tool
def check_data_coverage(question: str) -> dict[str, Any]:
    """Check whether the frozen CFPB snapshot can answer the requested question.

    The real version queries the snapshot manifest and a data-capability registry
    before any SQL or retrieval work happens.
    """
    q = question.lower()
    if "2023" in q:
        return {
            "answerable": False,
            "reason": "This frozen extract only covers 2024-01-01 through 2025-12-31.",
            "required_action": "abstain",
        }
    if "how much money" in q or "typically get back" in q:
        return {
            "answerable": False,
            "reason": (
                "The CFPB field 'Company response to consumer' is a response "
                "category, not a verified dollar amount of relief paid."
            ),
            "required_action": "abstain",
        }
    return {
        "answerable": True,
        "snapshot": "2024-01-01 to 2025-12-31, complaints with narratives",
    }


@tool
def resolve_company(company_or_brand: str) -> dict[str, Any]:
    """Resolve a consumer brand to the legal company stored in the CFPB data."""
    match = COMPANY_ALIASES.get(company_or_brand.strip().lower())
    return {
        "input": company_or_brand,
        "resolved_company": match or company_or_brand,
        "match_type": "curated_brand_alias" if match else "unverified_passthrough",
    }


@tool
def run_structured_analysis(analysis_request: str) -> dict[str, Any]:
    """Run a read-only analytical query and return its exact SQL plus result rows.

    MOCK ONLY: replace the body with a typed analysis-plan -> guarded DuckDB SQL
    compiler over gold tables. Never permit the model to execute arbitrary SQL.
    """
    return {
        "mock": True,
        "analysis_request": analysis_request,
        "sql": (
            "SELECT company_normalized, COUNT(*) AS q2_complaints, "
            "COUNT(*) AS q3_complaints FROM gold.company_month_metrics "
            "WHERE month >= DATE '2025-04-01' AND month < DATE '2025-10-01' "
            "GROUP BY 1 ORDER BY (q3_complaints - q2_complaints) DESC LIMIT 1"
        ),
        "rows": [
            {
                "company_normalized": "EXAMPLE BANK, INC.",
                "q2_complaints": 120,
                "q3_complaints": 260,
                "absolute_increase": 140,
            }
        ],
        "warning": "Mock result only; it is not derived from complaints.csv.",
    }


@tool
def retrieve_narratives(
    query: str, metadata_filters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Retrieve cited complaint narratives after applying hard metadata filters.

    MOCK ONLY: production first filters silver/gold Parquet by company/product/state/
    date, then runs hybrid BM25 + vector retrieval over the narrowed records.
    """
    return {
        "mock": True,
        "query": query,
        "metadata_filters": metadata_filters or {},
        "evidence": [
            {
                "complaint_id": 900001,
                "excerpt": (
                    "My account was frozen without warning and I could not access my paycheck."
                ),
            },
            {
                "complaint_id": 900002,
                "excerpt": (
                    "Repeated calls did not explain why the transfer was held "
                    "or when funds would return."
                ),
            },
        ],
        "warning": "Mock citations only; they are not real CFPB Complaint IDs.",
    }


TOOLS = [check_data_coverage, resolve_company, run_structured_analysis, retrieve_narratives]
MAX_TOOL_CALLS = 4

SYSTEM_PROMPT = """You are Meridian's complaint-intelligence assistant in a mock prototype.
You are a tool-using analyst, not a general chatbot.

Rules:
1. ALWAYS call check_data_coverage first. If it says answerable=false, stop and
   clearly abstain; do not call other tools or invent alternatives.
2. Call resolve_company when the question names a brand (Cash App, Zelle,
   TransUnion, or MOHELA).
3. For quantities, rankings, rates, trends, and comparisons, call
   run_structured_analysis. A real response must show the SQL returned by it.
4. For claims about consumer experiences, call retrieve_narratives and cite only
   the complaint IDs it returns.
5. For a question requiring a number AND the story behind it, call both tools.
6. Once sufficient tool evidence exists, answer in exactly this JSON shape:
   {"answer": "...", "citations": [123], "query": "SQL or null",
    "abstained": false, "limitations": ["..."]}
7. Never present mocked numbers or mocked citations as real CFPB facts. Clearly
   label this run as a mock until real tools are installed.
8. You have at most four tool calls. Synthesize or abstain before requesting a
   fifth; do not retry indefinitely.
"""


def call_agent(state: MessagesState) -> dict[str, list]:
    """The agentic node: model decides which approved tool to call next."""
    model = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)
    # OpenAI supports this flag and it keeps the evidence trail one tool call at a
    # time. The router below remains the defense-in-depth cap for all providers.
    response = model.bind_tools(TOOLS, parallel_tool_calls=False).invoke(
        [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    )
    return {"messages": [response]}


def bounded_tools_route(state: MessagesState) -> str:
    """Permit approved tool calls, but make the agent loop finite and auditable."""
    last_message = state["messages"][-1]
    pending_tool_calls = getattr(last_message, "tool_calls", [])
    completed_tool_calls = sum(isinstance(message, ToolMessage) for message in state["messages"])
    remaining_tool_calls = MAX_TOOL_CALLS - completed_tool_calls
    # Do not pass an oversized parallel batch to ToolNode: it would bypass the
    # configured cap even though the graph has only made one routing decision.
    if pending_tool_calls and len(pending_tool_calls) <= remaining_tool_calls:
        return "tools"
    return "end"


def build_graph():
    """The executable agent graph: an LLM-controlled, bounded tool loop."""
    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_agent)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", bounded_tools_route, {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")
    return graph.compile()


# This separate graph is intentionally a *diagram model*, not a second execution
# path. It makes the data/evidence lifecycle visible at a useful level of detail;
# the executable agent above still owns tool selection and looping.
class FlowState(TypedDict, total=False):
    next_step: Literal["continue", "abstain", "structured", "narrative", "combined"]


def noop(_: FlowState) -> dict:
    return {}


def coverage_route(state: FlowState) -> str:
    """In the real flow, the agent's plan and coverage result select this edge."""
    return state.get("next_step", "combined")


def build_explanation_graph():
    """Build a LangGraph-generated visual explanation of the assistant flow."""
    graph = StateGraph(FlowState)
    graph.add_node("agent_planner", noop)
    graph.add_node("coverage_check", noop)
    graph.add_node("abstain", noop)
    graph.add_node("safe_sql_analysis", noop)
    graph.add_node("filtered_narrative_retrieval", noop)
    graph.add_node("grounded_synthesis", noop)
    graph.add_node("citation_and_claim_validation", noop)

    graph.add_edge(START, "agent_planner")
    graph.add_edge("agent_planner", "coverage_check")
    graph.add_conditional_edges(
        "coverage_check",
        coverage_route,
        {
            "abstain": "abstain",
            "structured": "safe_sql_analysis",
            "narrative": "filtered_narrative_retrieval",
            # Combined questions intentionally get numbers first; their computed
            # entity/time window becomes a retrieval filter for the next node.
            "combined": "safe_sql_analysis",
        },
    )
    graph.add_edge("safe_sql_analysis", "filtered_narrative_retrieval")
    graph.add_edge("filtered_narrative_retrieval", "grounded_synthesis")
    graph.add_edge("grounded_synthesis", "citation_and_claim_validation")
    graph.add_edge("citation_and_claim_validation", END)
    graph.add_edge("abstain", END)
    return graph.compile()


def export_flow_diagram(
    output: Path = Path("artifacts/meridian_langgraph_flow.png"),
) -> Path:
    """Render the explanation graph as a PNG using LangGraph's Mermaid renderer."""
    output.parent.mkdir(parents=True, exist_ok=True)
    graph = build_explanation_graph().get_graph()
    output.write_bytes(graph.draw_mermaid_png())
    # Keep the Mermaid source too, so it can be reviewed or re-rendered locally.
    output.with_suffix(".mmd").write_text(graph.draw_mermaid(), encoding="utf-8")
    return output


def main() -> None:
    if "--diagram" in sys.argv:
        output = export_flow_diagram()
        print(f"LangGraph PNG flow diagram written to: {output}")
        return

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY before running this LangGraph mock-up.")

    cli_question = " ".join(arg for arg in sys.argv[1:] if not arg.startswith("--")).strip()
    question = cli_question or input("Ask a question: ").strip()
    result = build_graph().invoke({"messages": [HumanMessage(content=question)]})

    # The final AI message is the answers.json-compatible JSON requested above.
    final = result["messages"][-1].content
    print(final if isinstance(final, str) else json.dumps(final, indent=2))


if __name__ == "__main__":
    main()
