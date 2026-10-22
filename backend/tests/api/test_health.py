from app import __version__


async def test_health_returns_ok(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


async def test_ready_checks_db(client):
    r = await client.get("/api/v1/health/ready")
    assert r.status_code == 200 and r.json() == {"db": "ok"}
