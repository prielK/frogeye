"""Collector registry. Importing this package registers all built-in collectors."""

from app.collectors.base import Collector
from app.models.enums import SourceKind

_REGISTRY: dict[str, Collector] = {}


def register(collector: Collector) -> None:
    _REGISTRY[str(collector.kind)] = collector


def get_collector(kind: SourceKind | str) -> Collector:
    try:
        return _REGISTRY[str(kind)]
    except KeyError:
        raise KeyError(f"no collector registered for kind {str(kind)!r}") from None


def registered_kinds() -> set[str]:
    return set(_REGISTRY)


from app.collectors.edgar import EdgarCollector  # noqa: E402
from app.collectors.gdelt import GdeltCollector  # noqa: E402
from app.collectors.github_stats import (  # noqa: E402
    GithubReleasesCollector,
    GithubStatsCollector,
)
from app.collectors.google_news import GoogleNewsCollector  # noqa: E402
from app.collectors.hn import HnCollector  # noqa: E402
from app.collectors.jobs.collector import JobBoardCollector  # noqa: E402
from app.collectors.page_watch import PageWatchCollector  # noqa: E402
from app.collectors.rss import RssCollector  # noqa: E402

register(RssCollector())
register(GoogleNewsCollector())
register(GdeltCollector())
register(HnCollector())
register(GithubStatsCollector())
register(GithubReleasesCollector())
register(EdgarCollector())
register(JobBoardCollector())
register(PageWatchCollector())

__all__ = ["Collector", "SourceKind", "get_collector", "register", "registered_kinds"]
