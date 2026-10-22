"""Search-query construction for news collectors."""

from app.models import Competitor

MAX_ALIASES = 4


def build_competitor_query(c: Competitor) -> str:
    """`("A" OR "B") -"neg"...` from the first 4 strong (non-ambiguous) aliases, else the name."""
    ambiguous = set(c.ambiguous_aliases or [])
    strong = [a for a in (c.aliases or []) if a not in ambiguous][:MAX_ALIASES] or [c.name]
    query = "(" + " OR ".join(f'"{a}"' for a in strong) + ")"
    return query + "".join(f' -"{n}"' for n in (c.negative_keywords or []))
