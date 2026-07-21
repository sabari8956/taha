"""OpenAI client construction for semantic planning and narration."""

from __future__ import annotations

import os

from openai import OpenAI

DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def openai_client() -> OpenAI:
    """Create an OpenAI client without reading local dotenv files.

    The deployment supplies ``OPENAI_API_KEY`` through its environment. A custom
    compatible endpoint is accepted only through explicit ``OPENAI_BASE_URL``.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for LLM planning or narration")
    return OpenAI(
        api_key=api_key, base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
    )
