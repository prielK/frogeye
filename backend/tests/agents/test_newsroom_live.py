"""One tiny real-model newsroom pass (about a cent). Run with RUN_LIVE_LLM=1 and
OPENAI_API_KEY set: `pytest -m live_llm -v -s tests/agents/test_newsroom_live.py`.

Guards the claim/advice rule: with the claim/advice split and the JFrog brief as evidence, the
fact-checker must leave every role edition at least one takeaway on an ordinary day.
Each signal carries a stage-A action for every role: an edition's takeaways need one.
"""

import os
from datetime import UTC, datetime

import pytest

from app.agents.briefing_schema import EDITION_ROLES
from app.agents.graphs.newsroom import run_newsroom
from app.agents.runtime.accounted import AccountedLLM
from app.agents.runtime.context import RunCtx
from app.agents.runtime.openai_backend import OpenAIBackend
from app.agents.schemas import build_enums
from app.agents.tools.base import ToolDeps

ROLE_ACTIONS = {
    "sales": "Ask the account how it governs AI models today.",
    "pm": "Compare the launch with the JFrog roadmap.",
    "exec": "Review the competitive position at the next staff meeting.",
    "pmm_devrel": "Prepare a short comparison post for the community.",
}

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        not (os.environ.get("RUN_LIVE_LLM") == "1" and os.environ.get("OPENAI_API_KEY")),
        reason="live LLM test: needs RUN_LIVE_LLM=1 and OPENAI_API_KEY",
    ),
]


async def test_live_newsroom_editions_keep_a_takeaway(
    seeded, make_signal, session_factory, settings
):
    await make_signal(
        title="Sonatype launches a model registry for AI artifacts",
        fact="Sonatype launched a model registry that stores and scans AI models, on 14 October.",
        impact="Competes directly with JFrog's ML model management in Artifactory.",
        importance=90,
        signal_type="product_launch",
        stance="threat",
        competitors=["sonatype"],
        role_actions=ROLE_ACTIONS,
    )
    await make_signal(
        title="GitLab raises Premium seat prices by 20 percent",
        fact="GitLab raised the price of its Premium tier by 20 percent per seat.",
        impact="Gives JFrog sellers an opening with cost-sensitive GitLab Premium customers.",
        importance=80,
        signal_type="pricing_change",
        stance="opportunity",
        competitors=["gitlab"],
        role_actions=ROLE_ACTIONS,
    )
    await make_signal(
        title="CISA publishes updated SBOM minimum elements",
        fact="CISA published updated minimum elements for software bills of materials.",
        impact="Raises the bar for SBOM export, which JFrog Xray provides.",
        importance=70,
        signal_type="standard_spec",
        scope="industry",
        role_actions=ROLE_ACTIONS,
    )
    llm = AccountedLLM(OpenAIBackend(settings), session_factory, settings)
    deps = ToolDeps(session_factory=session_factory, llm=llm, settings=settings)
    result = await run_newsroom(
        session_factory,
        llm,
        deps,
        await build_enums(seeded),
        settings=settings,
        now=datetime.now(UTC),
        ctx=RunCtx(agent_run_id=None, node="newsroom"),
    )
    print(f"\n[live] newsroom status={result.status} cost=${result.cost_usd:.4f}")
    print(f"[live] master: {result.master.headline} | {len(result.master.top_takeaways)} takeaways")
    for role, edition in result.editions.items():
        print(f"[live] {role}: {edition.headline} | {len(edition.top_takeaways)} takeaways")
    assert result.status == "published", result.errors
    assert list(result.editions) == list(EDITION_ROLES)
    for role, edition in result.editions.items():
        assert len(edition.top_takeaways) >= 1, role
