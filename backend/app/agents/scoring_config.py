"""Scoring weights and tables. Editable; values follow the product brief."""

WEIGHTS = {
    "impact": 0.30,
    "novelty": 0.15,
    "competitor_tier": 0.15,
    "type": 0.15,
    "kit_priority": 0.10,
    "credibility": 0.10,
    "corroboration": 0.05,
}

TIER_W = {0: 0.5, 1: 1.0, 2: 0.7, 3: 0.4}  # tier 0 is JFrog
INDUSTRY_W = 0.6  # no competitor on the signal

TYPE_W = {
    "acquisition": 1.0,
    "product_launch": 0.9,
    "pricing_change": 0.9,
    "funding": 0.8,
    "security_incident": 0.8,
    "partnership": 0.7,
    "earnings_financials": 0.7,
    "customer_win_loss": 0.7,
    "analyst_recognition": 0.6,
    "feature_update": 0.6,
    "deprecation": 0.6,
    "leadership_change": 0.6,
    "legal_regulatory": 0.6,
    "messaging_change": 0.5,
    "industry_trend": 0.5,
    "standard_spec": 0.5,
    "hiring_trend": 0.4,
    "event_conference": 0.3,
    "community_buzz": 0.3,
    "other": 0.2,
}

KIT_PRIORITY_W = {1: 1.0, 2: 0.7, 3: 0.4}
CREDIBILITY_W = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.3, 5: 0.1, 6: 0.0}

HALF_LIFE_DAYS = 3
BREAKING_THRESHOLD = 85

# Evidence depth: a signal whose member documents ALL have less than THIN_BODY_CHARS of
# body text (e.g. Google News headline-only items) has its importance multiplied by
# THIN_EVIDENCE_MULTIPLIER and is not eligible for the briefing's top takeaways (themed
# sections still are). One fuller corroborating document lifts it on the next scoring.
THIN_BODY_CHARS = 200
THIN_EVIDENCE_MULTIPLIER = 0.6
# first-party sources are exempt. A terse note on a competitor's own channel (its
# releases, changelog, official blog or filings: the sources seeded with reliability A) is
# authoritative; the penalty targets third-party headline-only items (Google News etc.).
FIRST_PARTY_RELIABILITY = "A"
# Relevance: a signal whose CURRENT stage-B analysis rates no JFrog product at all
# (an empty jfrog_impact: once an Azure notice that led the ranking) has its
# importance multiplied by this, recorded as `product_impact` in the breakdown. Stage B
# re-scores the signals it just analysed, so it applies before that day's briefing.
NO_PRODUCT_IMPACT_MULTIPLIER = 0.7

DEFAULT_AFFINITY = 0.4
ROLE_AFFINITY: dict[str, dict[str, float]] = {
    "sales": {
        "pricing_change": 1.0,
        "customer_win_loss": 1.0,
        "messaging_change": 0.9,
        "product_launch": 0.7,
        "partnership": 0.6,
    },
    "pm": {
        "product_launch": 1.0,
        "feature_update": 1.0,
        "deprecation": 0.9,
        "security_incident": 0.7,
        "standard_spec": 0.7,
    },
    "exec": {
        "acquisition": 1.0,
        "funding": 1.0,
        "earnings_financials": 1.0,
        "partnership": 0.9,
        "analyst_recognition": 0.8,
        "leadership_change": 0.8,
        "legal_regulatory": 0.7,
    },
    "pmm_devrel": {
        "messaging_change": 1.0,
        "community_buzz": 1.0,
        "event_conference": 0.9,
        "analyst_recognition": 0.8,
        "product_launch": 0.6,
    },
}


def role_score(importance: int, signal_type: str | None, role: str) -> int:
    """Per-role re-rank of a signal's importance by role/type affinity. Role editions lead
    with their top stories by it, so a type outside the role's own (the default 0.4 keeps
    0.55 of the importance) falls well behind one inside them (1.0 keeps all of it)."""
    affinity = ROLE_AFFINITY.get(role, {}).get(signal_type or "", DEFAULT_AFFINITY)
    return round(importance * (0.25 + 0.75 * affinity))


# Exec's edition ranking leans on the analysis's own exec risk call (medium or none:
# unchanged), so among stories of close importance the higher-risk one leads.
EXEC_RISK_MULTIPLIER: dict[str, float] = {"high": 1.25, "low": 0.8}
# when an edition chooses its takeaways, a story with a current stage-B analysis has its
# edition rank scaled by this (its card carries the role's own angle and actions), so it wins
# a tie or a close call; a boost, not a rule, so an important story without one still leads.
# For Exec it compounds with EXEC_RISK_MULTIPLIER: an analysed low-risk story nets x0.92.
ANALYSED_TAKEAWAY_BOOST = 1.15


def edition_rank(
    importance: int, signal_type: str | None, role: str, risk_level: str | None = None
) -> float:
    """How a role edition ranks a story: its `role_score`, which for Exec is scaled by the
    analysis's exec risk level (`EXEC_RISK_MULTIPLIER`); unrounded, so it adds no ties."""
    score = role_score(importance, signal_type, role)
    if role == "exec":
        return score * EXEC_RISK_MULTIPLIER.get(risk_level or "", 1.0)
    return score
