"""Who published an item, in words, for readers.

Search-driven sources are generated per competitor ("JFrog · Google News"), which reads as
if the competitor published the story. Their collectors keep the real outlet in the raw
item's payload (Google News: `outlet`; GDELT: `domain`), so readers get "ET Now via
Google News" instead; every other source keeps its own name.
"""

import re
from typing import Any
from urllib.parse import urlsplit

from app.models.enums import SourceKind

AGGREGATORS = {
    SourceKind.google_news.value: ("Google News", "outlet"),
    SourceKind.gdelt.value: ("GDELT", "domain"),
    SourceKind.hn.value: ("Hacker News", None),
}


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
_BARE_HOST = re.compile(r"^[^\s/@:]+\.[^\s/@:]+(?::\d+)?(?:[/?#]\S*)?$")
_EMBEDDED_URL = re.compile(r"[a-z][a-z0-9+.-]*://([^\s/?#\[\]()]*)[^\s()\[\]]*", re.I)


def host_of(url: str) -> str:
    """An absolute URL's host without "www." ("" when it has none or does not parse)."""
    if not _SCHEME.match(url.strip()):
        return ""
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def is_url(text: str | None) -> bool:
    return bool(text) and bool(_SCHEME.match(text.strip()))


def display_title(title: str | None, url: str) -> str:
    """A title readers can see: the title, or "Untitled page on <host>" (the Feed's
    wording) when there is none or it is a URL. Never shows a scheme."""
    if title and title.strip() and not is_url(title):
        return title.strip()
    host = host_of(url)
    return f"Untitled page on {host}" if host else "Untitled page"


def _outlet_name(outlet: str) -> str:
    """An outlet that is really a URL (scheme, "www." or a bare host) as its bare host without
    "www."; any other outlet unchanged. Never returns a "://" string."""
    if _SCHEME.match(outlet) or outlet.lower().startswith("www.") or _BARE_HOST.match(outlet):
        try:
            host = urlsplit(outlet if "//" in outlet else f"//{outlet}").hostname or ""
        except ValueError:  # scraped junk such as "http://[bad": never raise for a label
            host = re.sub(r"[\[\]]", "", _SCHEME.sub("", outlet)).split("/", 1)[0].strip()
        host = host.removeprefix("www.")
        if host:
            return host.replace("://", "")
        return outlet.split("://", 1)[-1].strip("/ ") if "://" in outlet else outlet
    if "://" in outlet:  # a URL inside the name ("Example (https://example.com/x)")
        return _EMBEDDED_URL.sub(lambda m: m.group(1).removeprefix("www."), outlet)
    return outlet


def source_label(kind: str | None, name: str, payload: dict[str, Any] | None) -> str:
    """An aggregator item as "<outlet> via <aggregator>"; any other item as its source's name."""
    found = AGGREGATORS.get(str(kind)) if kind is not None else None
    if found is None:
        return name
    aggregator, key = found
    outlet = (payload or {}).get(key) if key else None
    if isinstance(outlet, str) and (name_ := _outlet_name(" ".join(outlet.split()))):
        return f"{name_} via {aggregator}"
    return aggregator
