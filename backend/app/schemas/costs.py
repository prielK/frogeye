from datetime import date

from pydantic import BaseModel, Field


class TaskCost(BaseModel):
    task: str
    calls: int
    cost_usd: float


class ModelCost(BaseModel):
    model: str
    calls: int
    cost_usd: float
    cached_calls: int


class DayCost(BaseModel):
    date: date
    cost_usd: float


class CostsOut(BaseModel):
    today_usd: float = Field(description="Spend in the current local (settings.timezone) day.")
    total_usd: float
    daily_budget_usd: float
    total_budget_usd: float
    by_task: list[TaskCost] = Field(description="All-time, costliest first.")
    by_model: list[ModelCost] = Field(description="All-time, costliest first.")
    last_7_days: list[DayCost] = Field(description="Local days, oldest first, ending today.")
