from sqlalchemy import select

from app.cli.apply_profile import apply_profile
from app.models import Competitor, Source
from app.seed.loader import load_seed


async def test_apply_profile_enables_disables_and_is_idempotent(session):
    await load_seed(session)
    # simulate a DB seeded under the strict-politeness profile
    names = {"Checkmarx Blog", "Aqua Blog", "Black Duck Blog"}
    for s in (await session.scalars(select(Source))).all():
        s.enabled = s.kind != "gdelt" and s.name not in names
        if s.kind == "sec_edgar":
            s.enabled = True
    await session.flush()

    changes = await apply_profile(session)

    sources = (await session.scalars(select(Source))).all()
    gdelt = [s for s in sources if s.kind == "gdelt"]
    edgar = [s for s in sources if s.kind == "sec_edgar"]
    assert gdelt and all(s.enabled for s in gdelt)
    assert edgar and not any(s.enabled for s in edgar)
    assert all(s.enabled for s in sources if s.name in names)
    assert set(changes["enabled"]) == names | {s.name for s in gdelt}
    assert set(changes["disabled"]) == {s.name for s in edgar}
    assert await apply_profile(session) == {"enabled": [], "disabled": []}


async def test_apply_profile_never_reenables_self_news(session):
    # a database seeded before the rule still holds JFrog's own feed and its GDELT
    # query (migration 0006 disabled them); the profile must not switch them back on.
    await load_seed(session)
    jf = await session.scalar(select(Competitor).where(Competitor.is_self))
    blog = Source(
        kind="rss",
        name="JFrog Blog",
        url="https://jfrog.com/blog/rss/",
        competitor_id=jf.id,
        reliability="A",
        enabled=False,
    )
    gdelt = Source(
        kind="gdelt",
        name="JFrog · GDELT",
        url="generated://gdelt/jfrog",
        competitor_id=jf.id,
        reliability="B",
        enabled=False,
    )
    session.add_all([blog, gdelt])
    await session.flush()

    changes = await apply_profile(session)

    await session.refresh(blog)
    await session.refresh(gdelt)
    assert blog.enabled is False and gdelt.enabled is False
    assert not {"JFrog Blog", "JFrog · GDELT"} & set(changes["enabled"])
