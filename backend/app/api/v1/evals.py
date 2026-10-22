"""The latest evaluation results (backend/evals): accuracy of triage, disambiguation,
retrieval, the guardrails, the fact-checker and the stored analyses, as measured by
`python -m evals.run` and committed with the code."""

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models import User
from app.schemas.evals import EvalsOut

router = APIRouter(prefix="/evals", tags=["evals"])

RESULTS_PATH = Path(__file__).resolve().parents[3] / "evals" / "results" / "latest.json"


@router.get("", response_model=EvalsOut)
async def get_evals(_: Annotated[User, Depends(get_current_user)]) -> EvalsOut:
    try:
        data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evaluation results yet") from None
    return EvalsOut.model_validate(data)
