"""Query the generation-pinned canonical-Silver narrative index."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meridian_assistant.retrieval import (  # noqa: E402
    DEFAULT_COLLECTION,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_INDEX,
    NarrativeFilters,
    retrieve_narratives,
)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--company")
    parser.add_argument("--product")
    parser.add_argument("--sub-product")
    parser.add_argument("--issue")
    parser.add_argument("--sub-issue")
    parser.add_argument("--state")
    parser.add_argument("--timely-response", choices=("true", "false"))
    parser.add_argument("--company-response")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--index-path", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    args = parser.parse_args()
    result = retrieve_narratives(
        args.query,
        NarrativeFilters(
            company=args.company,
            product=args.product,
            sub_product=args.sub_product,
            issue=args.issue,
            sub_issue=args.sub_issue,
            state=args.state,
            timely_response=None
            if args.timely_response is None
            else args.timely_response == "true",
            company_response=args.company_response,
            start=args.start,
            end=args.end,
        ),
        args.top_k,
        args.index_path,
        args.collection,
        args.embedding_model,
    )
    print(json.dumps(result.to_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
