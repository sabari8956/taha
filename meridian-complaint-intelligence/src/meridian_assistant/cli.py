"""Production command line interface for grounded answer and batch execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from meridian_assistant.api import create_app
from meridian_assistant.app import answer_question, default_dependencies, run_batch


def _factory(args: argparse.Namespace):
    return lambda: default_dependencies(
        gold_db=args.gold_db, manifest_path=args.bronze_manifest, index_root=args.rag_index
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian-assistant")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--bronze-manifest", type=Path, default=Path("bronze/current/manifest.json")
    )
    common.add_argument("--gold-db", type=Path, default=Path("gold/current/metrics.duckdb"))
    common.add_argument("--rag-index", type=Path, default=Path("artifacts/rag/chroma"))
    commands = parser.add_subparsers(dest="command", required=True)
    answer = commands.add_parser("answer", parents=[common])
    answer.add_argument("--id", required=True)
    answer.add_argument("--question", required=True)
    batch = commands.add_parser("batch", parents=[common])
    batch.add_argument("--questions", type=Path, required=True)
    batch.add_argument("--artifacts-root", type=Path, required=True)
    batch.add_argument("--run-id")
    serve = commands.add_parser("serve", parents=[common])
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--questions", type=Path, default=Path("data/questions.json"))
    serve.add_argument(
        "--cors", action="store_true", help="Allow cross-origin requests (local dev only)"
    )
    serve.add_argument(
        "--semantic-planner-model",
        default="gpt-5.6-luna",
        help="GPT-5.6 Luna semantic-planner model (default: gpt-5.6-luna)",
    )
    serve.add_argument(
        "--narrator-model",
        default="gpt-5.6-luna",
        help="GPT-5.6 Luna grounded-narrator model (default: gpt-5.6-luna)",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "answer":
            response = answer_question(args.id, args.question, dependencies_factory=_factory(args))
            print(json.dumps(response.answer.to_dict(), sort_keys=True))
        elif args.command == "batch":
            destination, _ = run_batch(
                args.questions,
                args.artifacts_root,
                run_id=args.run_id,
                dependencies_factory=_factory(args),
            )
            print(destination)
        else:
            create_app(
                gold_db=args.gold_db,
                bronze_manifest=args.bronze_manifest,
                rag_index=args.rag_index,
                questions_path=args.questions,
                enable_cors=args.cors,
                semantic_planner_model=args.semantic_planner_model,
                narrator_model=args.narrator_model,
            ).run(host=args.host, port=args.port)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0
