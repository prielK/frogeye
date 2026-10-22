"""What every ATS adapter shares: the adapter contract and the text/date/place helpers."""

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.collectors.base import CollectorError, PostingIn

DESCRIPTION_CHARS = 4000
UNTITLED = "Untitled role"


class BoardMissing(CollectorError):
    """The ATS has no such board: a 404/410, or a 2xx answer that is not a job listing."""


@dataclass(frozen=True)
class BoardPage:
    postings: list[PostingIn] = field(default_factory=list)
    # False when the ATS says the listing is partial: such a page closes no posting.
    complete: bool = True


class Adapter(Protocol):
    name: str  # the `config.ats` value
    label: str  # for source names: "Greenhouse"

    def url(self, token: str) -> str:
        """The board's public JSON endpoint."""
        ...

    def parse(self, data: Any) -> BoardPage:
        """Postings from the decoded JSON; raises BoardMissing when it is not a board."""
        ...


# -- text --------------------------------------------------------------------------------------

_DROP = re.compile(r"<\s*(script|style)\b.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
_BLOCK = re.compile(
    r"<\s*/?\s*(br|p|div|li|ul|ol|h[1-6]|tr|table|section|article|blockquote|hr)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"[^\S\n]+")


def text_or_none(value: Any) -> str | None:
    """A stripped string, or None for empty / non-string values."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def clip(text: str, limit: int = DESCRIPTION_CHARS) -> str:
    """At most ``limit`` characters, cut at a word boundary when one is near the end."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(" "), cut.rfind("\n"))
    if boundary > limit * 0.8:
        cut = cut[:boundary]
    return cut.rstrip()


def plain(value: Any) -> str | None:
    """Plain text: whitespace collapsed within lines, empty lines dropped, clipped."""
    text = text_or_none(value)
    if text is None:
        return None
    lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return clip("\n".join(line for line in lines if line)) or None


def html_to_text(value: Any, *, escaped: bool = False) -> str | None:
    """Plain text from an HTML fragment (``escaped``: the HTML itself is entity-escaped)."""
    markup = text_or_none(value)
    if markup is None:
        return None
    if escaped:
        markup = html.unescape(markup)
    markup = _DROP.sub(" ", markup)
    markup = _BLOCK.sub("\n", markup)
    return plain(html.unescape(_TAG.sub("", markup)))


# -- dates -------------------------------------------------------------------------------------


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def from_epoch_ms(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


# -- place -------------------------------------------------------------------------------------

_COUNTRY_NAMES = {
    "United States": ("united states", "united states of america", "usa", "us", "u.s", "u.s.a"),
    "United Kingdom": ("united kingdom", "uk", "u.k", "england", "scotland", "wales", "gb"),
    "Israel": ("israel", "il"),
    "Germany": ("germany", "de", "deutschland"),
    "France": ("france", "fr"),
    "Canada": ("canada", "ca"),
    "India": ("india", "in"),
    "Netherlands": ("netherlands", "the netherlands", "nl"),
    "Ireland": ("ireland", "ie"),
    "Spain": ("spain", "es"),
    "Portugal": ("portugal", "pt"),
    "Poland": ("poland", "pl"),
    "Czech Republic": ("czech republic", "czechia", "cz"),
    "Sweden": ("sweden", "se"),
    "Denmark": ("denmark", "dk"),
    "Switzerland": ("switzerland", "ch"),
    "Italy": ("italy", "it"),
    "Romania": ("romania", "ro"),
    "Ukraine": ("ukraine", "ua"),
    "Australia": ("australia", "au"),
    "New Zealand": ("new zealand", "nz"),
    "Japan": ("japan", "jp"),
    "Singapore": ("singapore", "sg"),
    "South Korea": ("south korea", "korea", "kr"),
    "Brazil": ("brazil", "br"),
    "Mexico": ("mexico", "mx"),
    "Argentina": ("argentina", "ar"),
    "Belgium": ("belgium", "be"),
    "Austria": ("austria", "at"),
    "Finland": ("finland", "fi"),
    "Norway": ("norway", "no"),
    "Estonia": ("estonia", "ee"),
    "Lithuania": ("lithuania", "lt"),
    "Greece": ("greece", "gr"),
    "Turkey": ("turkey", "türkiye", "tr"),
    "United Arab Emirates": ("united arab emirates", "uae", "ae"),
}
# ISO codes are trusted only from an explicit country field; inside a location string a
# two-letter part is more often a US state ("San Francisco, CA") than a country, so only
# names and the unambiguous "US" / "UK" count there.
_ISO = {alias: name for name, aliases in _COUNTRY_NAMES.items() for alias in aliases}
_IN_LOCATION = {a: n for a, n in _ISO.items() if len(a) > 2 or a in ("us", "uk")}
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
# State codes that are also ISO 3166-1 country codes: "Tel Aviv, IL" is Israel, "Chicago, IL"
# is Illinois. Such a code is read only together with a city known to be in one or the other;
# otherwise the country stays unknown (None), never a guess.
_STATE_OR_COUNTRY = {
    "AL": "Albania",
    "AR": "Argentina",
    "AZ": "Azerbaijan",
    "CA": "Canada",
    "CO": "Colombia",
    "DE": "Germany",
    "GA": "Gabon",
    "ID": "Indonesia",
    "IL": "Israel",
    "IN": "India",
    "KY": "Cayman Islands",
    "LA": "Laos",
    "MA": "Morocco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MN": "Mongolia",
    "MO": "Macao",
    "MS": "Montserrat",
    "MT": "Malta",
    "NC": "New Caledonia",
    "NE": "Niger",
    "PA": "Panama",
    "SC": "Seychelles",
    "SD": "Sudan",
    "TN": "Tunisia",
    "VA": "Vatican City",
}
# Cities that settle an ambiguous code, by country (comma-separated, lower case). For the US:
# the main tech-hiring cities of the ambiguous-code states.
_KNOWN_CITIES = {
    "United States": (
        "san francisco, south san francisco, los angeles, san diego, san jose, palo alto, "
        "mountain view, sunnyvale, santa clara, menlo park, redwood city, san mateo, "
        "foster city, burlingame, cupertino, oakland, berkeley, emeryville, sacramento, "
        "irvine, santa monica, pasadena, fremont, los gatos, boston, cambridge, "
        "somerville, waltham, burlington, lexington, woburn, framingham, chicago, "
        "evanston, naperville, springfield, indianapolis, carmel, denver, boulder, "
        "colorado springs, fort collins, broomfield, atlanta, alpharetta, philadelphia, "
        "pittsburgh, malvern, baltimore, bethesda, rockville, silver spring, arlington, "
        "reston, herndon, mclean, richmond, chantilly, raleigh, durham, charlotte, "
        "chapel hill, cary, morrisville, nashville, memphis, phoenix, scottsdale, tempe, "
        "tucson, chandler, minneapolis, st. paul, saint paul, st. louis, saint louis, "
        "kansas city, louisville, new orleans, wilmington, birmingham, huntsville, "
        "little rock, bentonville, boise, omaha, charleston, greenville, portland"
    ),
    "Israel": (
        "tel aviv, tel aviv-yafo, herzliya, haifa, jerusalem, petah tikva, petach tikva, "
        "ra'anana, raanana, netanya, beer sheva, be'er sheva, rehovot, yokneam, ramat gan, "
        "hod hasharon, kfar saba, caesarea"
    ),
    "Germany": (
        "berlin, munich, münchen, hamburg, frankfurt, cologne, köln, stuttgart, "
        "düsseldorf, dusseldorf, karlsruhe, leipzig, dresden"
    ),
    "India": (
        "bengaluru, bangalore, pune, hyderabad, chennai, mumbai, new delhi, delhi, "
        "gurgaon, gurugram, noida, ahmedabad, kolkata, kochi"
    ),
    "Canada": (
        "toronto, vancouver, montreal, montréal, ottawa, waterloo, calgary, edmonton, "
        "kitchener, halifax"
    ),
    "Colombia": "bogota, bogotá, medellin, medellín, cali",
    "Argentina": "buenos aires, cordoba, córdoba, rosario",
    "Indonesia": "jakarta, bandung, surabaya",
    "Malta": "valletta, sliema, st julian's",
}
_CITY_COUNTRY = {
    city.strip(): country for country, cities in _KNOWN_CITIES.items() for city in cities.split(",")
}
# "CA" is far more often California than Canada on a job board, so it reads
# as the United States unless the place is Canadian: a known Canadian city, or a province.
_CANADIAN_PROVINCES = frozenset(
    "ontario, on, quebec, québec, qc, british columbia, bc, alberta, ab, manitoba, mb, "
    "saskatchewan, sk, nova scotia, ns, new brunswick, nb, newfoundland and labrador, nl, "
    "prince edward island, pe, pei, yukon, yt, northwest territories, nt, nunavut, nu".split(", ")
)
# Every country name this module itself produces. An ATS's own country field can carry any
# text (`country_name` keeps an unknown name as given), so a reader that must not pass raw
# board text on (the weekly briefs) keeps only these.
KNOWN_COUNTRIES = frozenset({*_COUNTRY_NAMES, *_STATE_OR_COUNTRY.values(), *_KNOWN_CITIES})
_PLACE_PARTS = re.compile(r"[,;/|()–—]|\s-\s|\s-|-\s")


def _key(text: str) -> str:
    return text.strip().rstrip(".").lower()


def country_name(value: Any) -> str | None:
    """A country from an explicit country field: an ISO code or a name."""
    text = text_or_none(value)
    if text is None:
        return None
    return _ISO.get(_key(text), text if len(text) > 2 else text.upper())


def country_of(location: str | None) -> str | None:
    """The country a free-text location names, when it names one without doubt.

    A country name (or "US" / "UK") anywhere wins. Otherwise a trailing US state code means
    the United States, except for codes that are also a country's ("IL", "IN", "DE", ...):
    those need a known city ("Chicago, IL" -> United States, "Tel Aviv, IL" -> Israel) and are
    left unknown without one ("Peoria, IL" -> None). Unknown beats a wrong country. "CA" alone
    is California unless the place is Canadian, a known city or a province ("Santa Barbara,
    CA" -> United States, "Vancouver, BC, CA" -> Canada), or a known city of a third country
    ("Bogota, CA" -> None).
    """
    if not location:
        return None
    parts = [p.strip() for p in _PLACE_PARTS.split(location) if p and p.strip()]
    for part in reversed(parts):
        name = _IN_LOCATION.get(_key(part))
        if name:
            return name
    if len(parts) < 2:
        return None
    code = parts[-1].upper()
    if code not in _US_STATES:
        return None
    if code not in _STATE_OR_COUNTRY:
        return "United States"
    if code == "CA":
        before = [_key(p) for p in parts[:-1]]
        if any(_CITY_COUNTRY.get(p) not in (None, "United States", "Canada") for p in before):
            return None  # a third country's city ("Bogota, CA"): unknown, not a guess
        canadian = any(_CITY_COUNTRY.get(p) == "Canada" or p in _CANADIAN_PROVINCES for p in before)
        return "Canada" if canadian else "United States"
    city = _CITY_COUNTRY.get(_key(parts[-2]))
    if city in ("United States", _STATE_OR_COUNTRY[code]):
        return city
    return None


_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


def mentions_remote(*texts: str | None) -> bool:
    return any(t and _REMOTE.search(t) for t in texts)
