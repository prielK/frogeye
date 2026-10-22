from datetime import UTC, datetime

from app.models import DemoSnapshot


async def test_demo_snapshot_requires_auth(client, seeded):
    assert (await client.get("/api/v1/demo-snapshot")).status_code == 401


async def test_a_database_not_restored_from_the_snapshot_says_null(auth_client, seeded):
    r = await (await auth_client("sales")).get("/api/v1/demo-snapshot")
    assert r.status_code == 200 and r.json() == {"captured_at": None}


async def test_a_restored_database_serves_the_capture_time(auth_client, seeded, session):
    session.add(DemoSnapshot(captured_at=datetime(2026, 10, 22, 1, 13, tzinfo=UTC)))
    await session.flush()
    r = await (await auth_client("exec")).get("/api/v1/demo-snapshot")
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["captured_at"]) == datetime(
        2026, 10, 22, 1, 13, tzinfo=UTC
    )
