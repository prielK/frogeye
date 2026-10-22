import pytest
from pydantic import ValidationError

from app.agents.analysis_schema import JFROG_PRODUCTS, SignalAnalysisContent, make_analysis_model

REFS = ("doc:1", "kb:2")


def payload(cites=("doc:1",), **over):
    cited = {"text": "x", "cites": list(cites)}
    base = {
        "summary": cited,
        "what_happened": [cited],
        "background": [],
        "what_changed": [cited],
        "jfrog_impact": [
            {
                "product": "Xray",
                "effect": "threat",
                "severity": "high",
                "rationale": "r",
                "cites": list(cites),
            }
        ],
        "competitive_implications": [],
        "recommended_actions": {
            "sales": {
                "talk_track": ["t"],
                "discovery_questions": [],
                "objections": [{"objection": "o", "response": "r"}],
                "landmines": [],
            },
            "pm": {"assessment": [], "follow_ups": []},
            "exec": {"implications": [], "decisions": [], "risk_level": None},
            "pmm_devrel": {"messaging": [], "content_ideas": []},
        },
        "risks_opportunities": [{"kind": "risk", "text": "t", "cites": list(cites)}],
        "what_to_watch": [{"text": "t", "date": "2026-11-01", "cites": []}],
        "confidence_note": "ok",
    }
    return {**base, **over}


def test_analysis_model_accepts_known_refs():
    model = make_analysis_model(REFS)
    out = model.model_validate(payload(cites=("doc:1", "kb:2")))
    assert out.summary.cites == ["doc:1", "kb:2"]
    # the strict output loads into the persisted reader
    content = SignalAnalysisContent.model_validate(out.model_dump(mode="json"))
    assert content.jfrog_impact[0].product == "Xray"


def test_analysis_model_rejects_unknown_ref():
    model = make_analysis_model(REFS)
    with pytest.raises(ValidationError):
        model.model_validate(payload(summary={"text": "x", "cites": ["doc:999"]}))
    with pytest.raises(ValidationError):
        model.model_validate(payload(cites=("signal:7",)))


def test_analysis_model_rejects_unknown_product_and_extra_keys():
    model = make_analysis_model(REFS)
    bad = payload()
    bad["jfrog_impact"][0]["product"] = "Nexus"
    with pytest.raises(ValidationError):
        model.model_validate(bad)
    with pytest.raises(ValidationError):
        model.model_validate({**payload(), "extra": 1})
    nested = payload()
    nested["recommended_actions"]["pm"]["surprise"] = []
    with pytest.raises(ValidationError):
        model.model_validate(nested)


def _objects(node, defs):
    if isinstance(node, dict):
        if "$ref" in node:
            yield from _objects(defs[node["$ref"].split("/")[-1]], defs)
        if node.get("type") == "object" or "properties" in node:
            yield node
        for key, v in node.items():
            if key != "$ref":
                yield from _objects(v, defs)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v, defs)


def test_analysis_model_is_strict():
    schema = make_analysis_model(REFS).model_json_schema()
    objects = list(_objects(schema, schema.get("$defs", {})))
    assert len(objects) > 8
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
        for prop in obj["properties"].values():
            assert "default" not in prop


def test_empty_refs_builds_and_forbids_citations():
    model = make_analysis_model(())
    assert model.model_validate(payload(cites=()))
    with pytest.raises(ValidationError):
        model.model_validate(payload(cites=("doc:1",)))


def test_factory_is_cached_and_products_are_the_thirteen():
    assert make_analysis_model(REFS) is make_analysis_model(REFS)
    assert len(JFROG_PRODUCTS) == 13


def test_reader_tolerates_old_and_new_content():
    assert SignalAnalysisContent.model_validate({}).summary.text == ""
    content = SignalAnalysisContent.model_validate(
        {"summary": {"text": "s", "cites": ["doc:404"], "new": 1}, "future_field": [1]}
    )
    assert content.summary.cites == ["doc:404"]
    assert content.recommended_actions.exec.risk_level is None
