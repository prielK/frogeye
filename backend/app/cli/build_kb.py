"""Crawl the JFrog knowledge-base pages, index changed ones, print the stats as JSON.

Needs OPENAI_API_KEY (embeddings); exits 2 without it, before any fetch.
Usage: python -m app.cli.build_kb
"""

import asyncio
import dataclasses
import json
import sys

from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.openai_backend import OpenAIBackend
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.host_guard import DbHostGuard
from app.core.http import PoliteClient
from app.rag.kb import build_kb


async def _run() -> dict:
    settings = get_settings()
    llm = AccountedLLM(OpenAIBackend(settings), SessionLocal, settings)
    async with PoliteClient(settings, guard=DbHostGuard(SessionLocal)) as http:
        stats = await build_kb(SessionLocal, llm, http, ctx=RunCtx(agent_run_id=None, node="kb"))
    return dataclasses.asdict(stats)


def main() -> None:
    if not get_settings().openai_api_key:
        print("OPENAI_API_KEY is not set: the knowledge base needs embeddings. Nothing fetched.")
        sys.exit(2)
    print(json.dumps(asyncio.run(_run()), indent=2))


if __name__ == "__main__":
    main()
