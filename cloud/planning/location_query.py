"""Structured collection locations and provider-specific query formatting."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

REGION_NAMES = {
    "CA": {
        "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
        "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
        "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
        "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
        "SK": "Saskatchewan", "YT": "Yukon",
    },
    "US": {
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
    },
}
COUNTRY_NAMES = {"CA": "Canada", "US": "United States"}


class CollectionLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    region_code: str | None = None
    country_code: str

    @field_validator("country_code", "region_code")
    @classmethod
    def uppercase_code(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("country_code")
    @classmethod
    def supported_country(cls, value: str) -> str:
        if value not in COUNTRY_NAMES:
            raise ValueError(f"unsupported collection country: {value}")
        return value

    def validate_region(self) -> None:
        if self.region_code and self.region_code not in REGION_NAMES[self.country_code]:
            raise ValueError(
                f"unknown region {self.region_code} for country {self.country_code}"
            )


def provider_location(value: str | dict[str, Any], source: str) -> str:
    """Translate one stable location object into the provider's query convention."""

    if isinstance(value, str):
        return value
    location = CollectionLocation.model_validate(value)
    location.validate_region()
    country_name = COUNTRY_NAMES[location.country_code]
    region_name = (
        REGION_NAMES[location.country_code].get(location.region_code)
        if location.region_code
        else None
    )
    if source == "indeed":
        return (
            ", ".join(item for item in (location.city, location.region_code) if item)
            or country_name
        )
    return ", ".join(item for item in (location.city, region_name, country_name) if item)


def resolve_query_location(values: dict[str, Any], source: str) -> dict[str, Any]:
    resolved = dict(values)
    location = resolved.get("location")
    if isinstance(location, (str, dict)):
        resolved["location"] = provider_location(location, source)
    return resolved
