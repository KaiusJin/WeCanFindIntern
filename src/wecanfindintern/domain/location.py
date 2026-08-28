"""Location model and deterministic location normalization."""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel

from wecanfindintern.domain.normalization import normalize_text

COUNTRY_ALIASES = {
    "ca": "CA",
    "canada": "CA",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "uk": "GB",
    "united kingdom": "GB",
    "fr": "FR",
    "france": "FR",
}

CANADIAN_REGION_ALIASES = {
    "alberta": "AB",
    "ab": "AB",
    "british columbia": "BC",
    "bc": "BC",
    "manitoba": "MB",
    "mb": "MB",
    "new brunswick": "NB",
    "nb": "NB",
    "newfoundland and labrador": "NL",
    "newfoundland": "NL",
    "nl": "NL",
    "northwest territories": "NT",
    "nt": "NT",
    "nova scotia": "NS",
    "ns": "NS",
    "nunavut": "NU",
    "nu": "NU",
    "ontario": "ON",
    "ontraio": "ON",
    "ontairo": "ON",
    "on": "ON",
    "prince edward island": "PE",
    "pei": "PE",
    "pe": "PE",
    "quebec": "QC",
    "québec": "QC",
    "qc": "QC",
    "saskatchewan": "SK",
    "sk": "SK",
    "yukon": "YT",
    "yt": "YT",
}

US_REGION_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming", "DC": "District of Columbia",
}

US_REGION_ALIASES = {
    alias: code
    for code, name in US_REGION_NAMES.items()
    for alias in (code.casefold(), name.casefold())
}
US_REGION_ALIASES.update({
    "washington dc": "DC",
    "washington d c": "DC",
    "district columbia": "DC",
})

CANADIAN_REGION_NAMES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NT": "Northwest Territories",
    "NS": "Nova Scotia",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}

COUNTRY_NAMES = {
    "CA": "Canada",
    "US": "United States",
    "GB": "United Kingdom",
    "FR": "France",
}

CANADIAN_CITY_NAMES = {
    ("QC", "montreal"): "Montréal",
    ("QC", "quebec"): "Québec",
}


class Location(BaseModel):
    raw: str | None = None
    city: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    region_type: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    normalized: str = ""


def parse_location(value: str | None) -> Location:
    if not value:
        return Location()
    raw = value.strip()
    if normalize_text(raw) in {"remote", "worldwide"}:
        return Location(raw=raw, normalized="remote")
    country_only = COUNTRY_ALIASES.get(normalize_text(raw))
    if country_only:
        return Location(
            raw=raw,
            country_code=country_only,
            country_name=COUNTRY_NAMES.get(country_only),
            normalized=normalize_text(country_only),
        )

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    city_raw = parts[0] if parts else None
    region_raw = parts[-2] if len(parts) >= 2 else None
    country_token = normalize_text(parts[-1]) if len(parts) >= 2 else ""
    country = COUNTRY_ALIASES.get(country_token)

    if country is None and len(parts) == 2:
        # JobSpy sometimes returns "City, Region" without a country.
        region_raw = parts[-1]
        normalized_region = normalize_text(region_raw)
        if normalized_region in CANADIAN_REGION_ALIASES:
            country = "CA"
        elif normalized_region in US_REGION_ALIASES:
            country = "US"
    elif country is None and len(parts) >= 3 and len(parts[-1]) == 2:
        country = parts[-1].upper()

    region = normalize_region_code(region_raw, country)
    region_name = normalize_region_name(region_raw, region, country)
    region_type = derive_region_type(region, country)
    country_name = COUNTRY_NAMES.get(country) if country else None
    city = normalize_city_name(city_raw, region, country)

    normalized_parts = [normalize_text(item) for item in (city, region, country) if item]
    return Location(
        raw=raw,
        city=city,
        region_code=region,
        region_name=region_name,
        region_type=region_type,
        country_code=country,
        country_name=country_name,
        normalized="|".join(normalized_parts),
    )


def normalize_region_code(value: str | None, country_code: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_text(value)
    if country_code == "CA" or normalized in CANADIAN_REGION_ALIASES:
        return CANADIAN_REGION_ALIASES.get(normalized, value.strip().upper())
    if country_code == "US" or normalized in US_REGION_ALIASES:
        return US_REGION_ALIASES.get(normalized, value.strip().upper())
    return value.strip().upper()


def normalize_city_name(
    raw_value: str | None,
    region_code: str | None,
    country_code: str | None,
) -> str | None:
    if not raw_value:
        return None
    value = raw_value.strip()
    if country_code == "CA" and region_code:
        ascii_key = "".join(
            character
            for character in unicodedata.normalize("NFKD", value).casefold()
            if not unicodedata.combining(character)
        )
        return CANADIAN_CITY_NAMES.get((region_code, ascii_key), value)
    return value


def normalize_region_name(
    raw_value: str | None,
    region_code: str | None,
    country_code: str | None,
) -> str | None:
    if country_code == "CA" and region_code:
        return CANADIAN_REGION_NAMES.get(region_code, raw_value)
    if country_code == "US" and region_code:
        return US_REGION_NAMES.get(region_code, raw_value)
    return raw_value.strip() if raw_value else None


def derive_region_type(region_code: str | None, country_code: str | None) -> str | None:
    if not region_code:
        return None
    if country_code == "CA":
        return "territory" if region_code in {"NT", "NU", "YT"} else "province"
    if country_code == "US":
        return "state"
    return "region"
