import logging

import jwt
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.security import create_session_token, decode_session_token, warn_if_weak_secret
from app.models import User

# A signing key that is NOT the app's, long enough for HS256 (RFC 7518 3.2: >= 32 bytes).
OTHER_SECRET = "another-signing-key-for-tests-only-32b"


async def test_login_sets_cookie(client, seeded):
    r = await client.post(
        "/api/v1/auth/login", json={"email": "pm@frogeye.local", "password": "frogeye-demo"}
    )
    assert r.status_code == 200 and r.json()["role"] == "pm"
    assert r.json()["timezone"] == get_settings().timezone  # the app's day, not the browser's
    assert "password_hash" not in r.json()
    assert "frogeye_session" in r.cookies
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc


async def test_wrong_password_401(client, seeded):
    r = await client.post("/api/v1/auth/login", json={"email": "pm@frogeye.local", "password": "x"})
    assert r.status_code == 401


async def test_unknown_email_same_401(client, seeded):
    bad_pw = await client.post(
        "/api/v1/auth/login", json={"email": "pm@frogeye.local", "password": "x"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@frogeye.local", "password": "x"}
    )
    assert unknown.status_code == 401
    assert unknown.json() == bad_pw.json()


async def test_me_requires_cookie(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_me_returns_user_after_login(auth_client):
    c = await auth_client("exec")
    r = await c.get("/api/v1/auth/me")
    assert r.status_code == 200 and r.json()["role"] == "exec"
    assert r.json()["timezone"] == get_settings().timezone == "Asia/Jerusalem"


async def test_me_invalid_token_401(client):
    client.cookies.set("frogeye_session", "garbage")
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_me_deleted_user_401(auth_client, session):
    c = await auth_client("sales")
    await session.execute(delete(User).where(User.email == "sales@frogeye.local"))
    await session.flush()
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_logout_clears_cookie(auth_client):
    c = await auth_client("pm")
    r = await c.post("/api/v1/auth/logout")
    assert r.status_code == 204
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_token_claims_roundtrip():
    claims = decode_session_token(create_session_token(7, "pm"))
    assert claims.sub == 7 and claims.role == "pm"
    assert len(OTHER_SECRET.encode()) >= 32
    bad = jwt.encode({"sub": "7", "role": "pm"}, OTHER_SECRET, algorithm="HS256")
    try:
        decode_session_token(bad)
    except jwt.PyJWTError:
        pass
    else:
        raise AssertionError("bad signature accepted")
    assert get_settings().jwt_secret != OTHER_SECRET


def test_short_jwt_secret_warns_at_startup(caplog):
    """A startup warning, not a hard failure: .env.example ships a placeholder secret."""
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        warn_if_weak_secret("change-me")
    assert any("JWT_SECRET" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        warn_if_weak_secret("x" * 32)
    assert caplog.records == []


async def test_require_roles_forbids_other_roles(app_with_route, auth_client):
    sales = await auth_client("sales")
    analyst = await auth_client("analyst")
    assert (await sales.get("/api/v1/_t")).status_code == 403
    assert (await analyst.get("/api/v1/_t")).status_code == 200


async def test_require_roles_unauthenticated_401(app_with_route, client):
    assert (await client.get("/api/v1/_t")).status_code == 401


async def test_personas_listed_in_demo(client, seeded):
    r = await client.get("/api/v1/auth/personas")
    assert [p["role"] for p in r.json()] == ["analyst", "sales", "pm", "exec", "pmm_devrel"]
    assert set(r.json()[0]) == {"name", "email", "role"}


async def test_personas_hidden_with_real_users(client, seeded, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert (await client.get("/api/v1/auth/personas")).status_code == 200
    session.add(User(email="real@jfrog.com", name="Real", role="pm", password_hash="x"))
    await session.flush()
    assert (await client.get("/api/v1/auth/personas")).status_code == 404
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    assert (await client.get("/api/v1/auth/personas")).status_code == 200
