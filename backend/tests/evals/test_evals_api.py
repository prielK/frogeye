import json

from app.api.v1 import evals as evals_api
from app.schemas.evals import EvalsOut


def payload() -> dict:
    metric = {
        "key": "accuracy",
        "label": "Accuracy",
        "value": 0.5,
        "numerator": 1,
        "denominator": 2,
    }
    return {
        "schema_version": 1,
        "run_at": "2026-10-21T19:00:00Z",
        "mode": "record",
        "recorded_at": "2026-10-21T18:47:14Z",
        "models": {"fast": "f", "smart": "s", "embedding": "e", "judge": "s"},
        "cost": {"live_usd": 0.02, "recorded_usd": 0.0, "live_calls": 3, "replayed_calls": 0},
        "stale_cases": 0,
        "suites": [
            {
                "name": "triage",
                "title": "Triage",
                "description": "d",
                "label_source": "l",
                "cases": 2,
                "scored": 2,
                "stale": 0,
                "metrics": [metric],
                "groups": [{"name": "hard_negative", "cases": 1, "metrics": [metric]}],
                "failures": [{"case_id": "triage-01", "expected": "a", "got": "b", "detail": None}],
                "stale_cases": [],
            }
        ],
    }


async def test_evals_requires_a_session(client, seeded):
    assert (await client.get("/api/v1/evals")).status_code == 401


async def test_evals_serves_the_results_file(auth_client, monkeypatch, tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    monkeypatch.setattr(evals_api, "RESULTS_PATH", path)
    r = await (await auth_client("sales")).get("/api/v1/evals")
    assert r.status_code == 200
    body = r.json()
    assert body["suites"][0]["metrics"][0] == payload()["suites"][0]["metrics"][0]
    assert body["cost"]["live_usd"] == 0.02 and body["mode"] == "record"


async def test_evals_404_without_results(auth_client, monkeypatch, tmp_path):
    monkeypatch.setattr(evals_api, "RESULTS_PATH", tmp_path / "missing.json")
    assert (await (await auth_client("pm")).get("/api/v1/evals")).status_code == 404


def test_the_committed_results_match_the_schema():
    data = json.loads(evals_api.RESULTS_PATH.read_text(encoding="utf-8"))
    out = EvalsOut.model_validate(data)
    assert {s.name for s in out.suites} == {
        "triage",
        "disambiguation",
        "retrieval",
        "guardrails",
        "fact_checker",
        "faithfulness",
    }
