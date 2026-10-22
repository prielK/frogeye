from pydantic import BaseModel


class CompetitorOut(BaseModel):
    id: int
    slug: str
    name: str
    tier: int
    relationship: str
    is_self: bool
    website: str | None
    ticker: str | None
    description: str | None
    logo_url: str | None
