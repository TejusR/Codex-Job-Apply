from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .search import infer_source_from_hostname

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gad_source",
    "gh_src",
    "gh_jid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "source",
    "trk",
}

DEFAULT_ROLE_KEYWORDS = (
    "software engineer",
    "software developer",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "full-stack engineer",
    "platform engineer",
)

US_STATE_NAMES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
}

US_STATE_ABBREVIATIONS = {
    "al",
    "ak",
    "az",
    "ar",
    "ca",
    "co",
    "ct",
    "de",
    "fl",
    "ga",
    "hi",
    "id",
    "il",
    "in",
    "ia",
    "ks",
    "ky",
    "la",
    "me",
    "md",
    "ma",
    "mi",
    "mn",
    "ms",
    "mo",
    "mt",
    "ne",
    "nv",
    "nh",
    "nj",
    "nm",
    "ny",
    "nc",
    "nd",
    "oh",
    "ok",
    "or",
    "pa",
    "ri",
    "sc",
    "sd",
    "tn",
    "tx",
    "ut",
    "vt",
    "va",
    "wa",
    "wv",
    "wi",
    "wy",
    "dc",
}

DATE_ONLY_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)

DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %I:%M %p",
    "%b %d, %Y %I:%M %p",
    "%B %d, %Y %I:%M %p",
)


@dataclass(slots=True)
class FreshnessCheck:
    raw_value: str | None
    is_recent: bool
    is_verifiable: bool
    normalized_posted_at: str | None
    reason: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonicalize_url(raw_url: str, canonical_url: str | None = None) -> str:
    source = (canonical_url or raw_url).strip()
    parts = urlsplit(source)
    if not parts.scheme:
        parts = urlsplit(f"https://{source}")

    hostname = (parts.hostname or "").lower()
    port = parts.port
    scheme = (parts.scheme or "https").lower()

    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")

    kept_query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith(TRACKING_QUERY_PREFIXES):
            continue
        if lowered in TRACKING_QUERY_KEYS:
            continue
        kept_query_items.append((key, value))
    query = urlencode(sorted(kept_query_items))
    return urlunsplit((scheme, netloc, path, query, ""))


def build_job_key(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return f"url-{digest[:24]}"


def infer_source(url: str) -> str:
    hostname = urlsplit(url).hostname or ""
    return infer_source_from_hostname(hostname)


def title_matches_role(title: str | None, role_keywords: list[str] | None = None) -> bool:
    if not title:
        return False
    lowered = title.lower()
    normalized = lowered.replace("-", " ").replace("/", " ")
    for keyword in role_keywords or list(DEFAULT_ROLE_KEYWORDS):
        candidate = keyword.lower()
        if candidate in lowered or candidate in normalized:
            return True
    return False


def location_matches_us(location: str | None, allowed_locations: list[str] | None = None) -> bool:
    if not location:
        return False
    lowered = location.lower()
    if allowed_locations:
        for value in allowed_locations:
            if value.lower() in lowered:
                return True
    if "united states" in lowered or "usa" in lowered or "u.s." in lowered:
        return True
    if "remote" in lowered and ("us" in lowered or "united states" in lowered):
        return True
    for state_name in US_STATE_NAMES:
        if state_name in lowered:
            return True
    tokens = {
        token.strip(" .,()[]")
        for token in lowered.replace("/", " ").replace("-", " ").split()
    }
    return any(token in US_STATE_ABBREVIATIONS for token in tokens)


def evaluate_posted_at(
    raw_value: str | None, *, now: datetime | None = None
) -> FreshnessCheck:
    if raw_value is None or not raw_value.strip():
        return FreshnessCheck(None, False, False, None, "missing_posted_date")

    current = now or utc_now()
    raw = raw_value.strip()
    lowered = raw.lower()

    if lowered in {"today", "just now"}:
        return FreshnessCheck(raw, True, True, format_timestamp(current), "relative_today")
    if lowered == "yesterday":
        return FreshnessCheck(raw, False, False, None, "date_is_only_yesterday")

    relative = _parse_relative_time(raw, current)
    if relative is not None:
        delta = current - relative
        return FreshnessCheck(
            raw,
            delta <= timedelta(hours=24),
            True,
            format_timestamp(relative),
            "relative_time",
        )

    absolute = _parse_datetime(raw, current)
    if absolute is not None:
        delta = current - absolute
        return FreshnessCheck(
            raw,
            timedelta(0) <= delta <= timedelta(hours=24),
            True,
            format_timestamp(absolute),
            "absolute_datetime",
        )

    date_only = _parse_date_only(raw, current)
    if date_only is not None:
        if date_only == current.date():
            assumed = datetime.combine(date_only, time.min, tzinfo=current.tzinfo)
            return FreshnessCheck(
                raw,
                True,
                True,
                format_timestamp(assumed),
                "same_day_date_only",
            )
        return FreshnessCheck(raw, False, False, None, "date_only_is_not_same_day")

    return FreshnessCheck(raw, False, False, None, "unrecognized_posted_date")


def _parse_relative_time(raw: str, now: datetime) -> datetime | None:
    tokens = raw.lower().split()
    if len(tokens) < 2 or "ago" not in tokens:
        return None

    try:
        quantity = float(tokens[0])
    except ValueError:
        return None

    unit = tokens[1]
    if unit.startswith("hour"):
        return now - timedelta(hours=quantity)
    if unit.startswith("day"):
        return now - timedelta(days=quantity)
    if unit.startswith("minute"):
        return now - timedelta(minutes=quantity)
    return None


def _parse_datetime(raw: str, now: datetime) -> datetime | None:
    cleaned = raw.replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(cleaned)
        if value.tzinfo is None:
            value = value.replace(tzinfo=now.tzinfo)
        return value.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in DATETIME_FORMATS:
        try:
            value = datetime.strptime(raw, fmt).replace(tzinfo=now.tzinfo)
            return value.astimezone(timezone.utc)
        except ValueError:
            continue

    try:
        value = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=now.tzinfo)
    return value.astimezone(timezone.utc)


def _parse_date_only(raw: str, now: datetime) -> date | None:
    for fmt in DATE_ONLY_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None
