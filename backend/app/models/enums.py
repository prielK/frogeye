from enum import StrEnum


class Relationship(StrEnum):
    competitor = "competitor"
    partner = "partner"
    frenemy = "frenemy"
    self = "self"


class SourceKind(StrEnum):
    rss = "rss"
    google_news = "google_news"
    gdelt = "gdelt"
    hn = "hn"
    github_releases = "github_releases"
    github_repo_stats = "github_repo_stats"
    sec_edgar = "sec_edgar"
    page_watch = "page_watch"
    job_board = "job_board"
    oss_stats = "oss_stats"
    tavily = "tavily"


class Reliability(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class Role(StrEnum):
    analyst = "analyst"
    sales = "sales"
    pm = "pm"
    exec = "exec"
    pmm_devrel = "pmm_devrel"


class DocStatus(StrEnum):
    new = "new"
    irrelevant = "irrelevant"
    relevant = "relevant"
    duplicate = "duplicate"


class Scope(StrEnum):
    competitor = "competitor"
    industry = "industry"
    jfrog = "jfrog"


class RunStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class SourceHealth(StrEnum):
    ok = "ok"
    degraded = "degraded"
    down = "down"


class SignalType(StrEnum):
    product_launch = "product_launch"
    feature_update = "feature_update"
    deprecation = "deprecation"
    pricing_change = "pricing_change"
    messaging_change = "messaging_change"
    partnership = "partnership"
    funding = "funding"
    acquisition = "acquisition"
    earnings_financials = "earnings_financials"
    leadership_change = "leadership_change"
    hiring_trend = "hiring_trend"
    customer_win_loss = "customer_win_loss"
    security_incident = "security_incident"
    analyst_recognition = "analyst_recognition"
    community_buzz = "community_buzz"
    event_conference = "event_conference"
    legal_regulatory = "legal_regulatory"
    industry_trend = "industry_trend"
    standard_spec = "standard_spec"
    other = "other"


class Stance(StrEnum):
    threat = "threat"
    opportunity = "opportunity"
    neutral = "neutral"


class SignalStatus(StrEnum):
    pending = "pending"
    auto = "auto"
    needs_review = "needs_review"
    approved = "approved"
    rejected = "rejected"
    suppressed = "suppressed"


class BriefingEdition(StrEnum):
    master = "master"
    sales = "sales"
    pm = "pm"
    exec = "exec"
    pmm_devrel = "pmm_devrel"


class BriefingStatus(StrEnum):
    published = "published"
    quiet = "quiet"
    skipped_budget = "skipped_budget"
    failed = "failed"


# Briefings readers get: published or quiet. They carry published_at, are emailed, are served
# first by the read API and count as briefed for the next selection.
LIVE_BRIEFING_STATUSES = (BriefingStatus.published.value, BriefingStatus.quiet.value)


class AgentRunStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    interrupted = "interrupted"


class StepKind(StrEnum):
    llm = "llm"
    tool = "tool"
    node = "node"


class FactKind(StrEnum):
    """What a company fact states (competitor intelligence)."""

    funding_round = "funding_round"
    valuation = "valuation"
    headcount = "headcount"
    layoff = "layoff"
    revenue = "revenue"
    arr = "arr"
    customer = "customer"
    customer_count = "customer_count"
    market_share = "market_share"
    acquisition = "acquisition"
    leadership_change = "leadership_change"
    partnership = "partnership"
    office = "office"


class FactSourceKind(StrEnum):
    """Where a company fact's quote comes from (`company_facts.source_id` points into it)."""

    document = "document"
    page_snapshot = "page_snapshot"


class PageKind(StrEnum):
    """Which watched company page a snapshot belongs to."""

    pricing = "pricing"
    about = "about"
    careers = "careers"


class RetrievalKind(StrEnum):
    article_chunk = "article_chunk"
    signal = "signal"
    jfrog_kb = "jfrog_kb"
