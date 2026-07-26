"""Heuristics for pulling a show date and venue/place out of titles and descriptions.

Uploaders write dates in every imaginable format, so we try a series of
patterns and normalise everything to ISO (YYYY-MM-DD).  A partial date
(year only, or year-month) is still useful for grouping duplicates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


@dataclass
class ShowInfo:
    date: Optional[str] = None       # "2016-06-14", "2016-06" or "2016"
    venue: Optional[str] = None      # free text: venue and/or city
    tour: Optional[str] = None
    sources: dict = field(default_factory=dict)

    @property
    def date_precision(self) -> int:
        """0 = none, 1 = year, 2 = year-month, 3 = full date."""
        if not self.date:
            return 0
        return self.date.count("-") + 1


def _iso(y: int, m: Optional[int] = None, d: Optional[int] = None) -> Optional[str]:
    if y < 100:
        y += 2000 if y < 50 else 1900
    if not 1950 <= y <= 2100:  # reject obviously bogus years
        return None
    if m is None:
        return f"{y:04d}"
    if not 1 <= m <= 12:
        return None
    if d is None:
        return f"{y:04d}-{m:02d}"
    if not 1 <= d <= 31:
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def extract_date(text: str) -> Optional[str]:
    """Return the best ISO-ish date found in *text*, or None."""
    t = text.lower()

    # 2016-06-14 / 2016.06.14 / 2016/06/14
    m = re.search(r"\b((?:19|20)\d{2})[./-](\d{1,2})[./-](\d{1,2})\b", t)
    if m:
        iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso

    # 14-06-2016 / 06/14/2016 (ambiguous day/month: try month-first, then day-first)
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})\b", t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        iso = _iso(y, a, b) or _iso(y, b, a)
        if iso:
            return iso

    # June 14, 2016 / June 14th 2016
    m = re.search(_MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})", t)
    if m:
        iso = _iso(int(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)))
        if iso:
            return iso

    # 14 June 2016 / 14th of June, 2016
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH_RE + r"\.?,?\s+((?:19|20)\d{2})", t)
    if m:
        iso = _iso(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
        if iso:
            return iso

    # June 2016
    m = re.search(_MONTH_RE + r"\.?,?\s+((?:19|20)\d{2})", t)
    if m:
        iso = _iso(int(m.group(2)), MONTHS[m.group(1)[:3]])
        if iso:
            return iso

    # bare year, e.g. "Lollapalooza 2015"
    m = re.search(r"\b((?:19|20)\d{2})\b", t)
    if m:
        return _iso(int(m.group(1)))
    return None


# Festivals / venues that identify a show even without "live at ..." phrasing.
KNOWN_EVENTS = [
    "lollapalooza", "bonnaroo", "coachella", "reading festival", "leeds festival",
    "lowlands", "pinkpop", "rock am ring", "rock im park", "southside", "hurricane",
    "firefly", "boston calling", "hangout fest", "voodoo", "austin city limits",
    "acl fest", "summer sonic", "fuji rock", "openair", "sziget", "mtv",
    "madison square garden", "red rocks", "wembley", "o2 arena", "schottenstein",
    "nationwide arena", "newport music hall", "the basement", "house of blues",
]

_VENUE_PATTERNS = [
    r"live\s+(?:at|@|in|from)\s+(?:the\s+)?([^|(\[\]\-–—]{3,60})",
    r"@\s*(?:the\s+)?([A-Z][^|(\[\]–—]{3,60})",
]


def extract_venue(text: str) -> Optional[str]:
    for ev in KNOWN_EVENTS:
        if ev in text.lower():
            # keep the surrounding phrase capitalised as written
            m = re.search(re.escape(ev), text, re.IGNORECASE)
            if m:
                return text[m.start():m.start() + len(ev)].strip()
    for pat in _VENUE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE if pat.startswith("live") else 0)
        if m:
            venue = m.group(1).strip(" .,-|")
            # cut trailing date fragments ("... on June 14 2016")
            venue = re.split(r"\b(?:on\s+)?" + _MONTH_RE + r"|\b(?:19|20)\d{2}\b|\d{1,2}[./-]\d{1,2}",
                             venue, 1, flags=re.IGNORECASE)[0].strip(" .,-|")
            if len(venue) >= 3:
                return venue
    return None


def extract_show_info(title: str, description: str = "") -> ShowInfo:
    """Combine title (preferred) and description (fallback) into a ShowInfo."""
    info = ShowInfo()
    info.date = extract_date(title)
    info.venue = extract_venue(title)
    if description:
        if not info.date:
            info.date = extract_date(description[:2000])
        if not info.venue:
            info.venue = extract_venue(description[:2000])
    return info
