from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from datetime import date
from enum import Enum
from pydantic import BaseModel


class JobType(Enum):
    FULL_TIME = (
        "fulltime",
        "períodointegral",
        "estágio/trainee",
        "cunormăîntreagă",
        "tiempocompleto",
        "vollzeit",
        "voltijds",
        "tempointegral",
        "全职",
        "plnýúvazek",
        "fuldtid",
        "دوامكامل",
        "kokopäivätyö",
        "tempsplein",
        "vollzeit",
        "πλήρηςαπασχόληση",
        "teljesmunkaidő",
        "tempopieno",
        "tempsplein",
        "heltid",
        "jornadacompleta",
        "pełnyetat",
        "정규직",
        "100%",
        "全職",
        "งานประจำ",
        "tamzamanlı",
        "повназайнятість",
        "toànthờigian",
    )
    PART_TIME = ("parttime", "teilzeit", "částečnýúvazek", "deltid")
    CONTRACT = ("contract", "contractor")
    TEMPORARY = ("temporary",)
    INTERNSHIP = (
        "internship",
        "prácticas",
        "ojt(onthejobtraining)",
        "praktikum",
        "praktik",
    )

    PER_DIEM = ("perdiem",)
    NIGHTS = ("nights",)
    OTHER = ("other",)
    SUMMER = ("summer",)
    VOLUNTEER = ("volunteer",)


class Country(Enum):
    """
    Indeed country names, subdomains, and API country codes.
    """

    ARGENTINA = ("argentina", "ar")
    AUSTRALIA = ("australia", "au")
    AUSTRIA = ("austria", "at")
    BAHRAIN = ("bahrain", "bh")
    BANGLADESH = ("bangladesh", "bd")  # Added Bangladesh
    BELGIUM = ("belgium", "be")
    BULGARIA = ("bulgaria", "bg")
    BRAZIL = ("brazil", "br")
    CANADA = ("canada", "ca")
    CHILE = ("chile", "cl")
    CHINA = ("china", "cn")
    COLOMBIA = ("colombia", "co")
    COSTARICA = ("costa rica", "cr")
    CROATIA = ("croatia", "hr")
    CYPRUS = ("cyprus", "cy")
    CZECHREPUBLIC = ("czech republic,czechia", "cz")
    DENMARK = ("denmark", "dk")
    ECUADOR = ("ecuador", "ec")
    EGYPT = ("egypt", "eg")
    ESTONIA = ("estonia", "ee")
    FINLAND = ("finland", "fi")
    FRANCE = ("france", "fr")
    GERMANY = ("germany", "de")
    GREECE = ("greece", "gr")
    HONGKONG = ("hong kong", "hk")
    HUNGARY = ("hungary", "hu")
    INDIA = ("india", "in")
    INDONESIA = ("indonesia", "id")
    IRELAND = ("ireland", "ie")
    ISRAEL = ("israel", "il")
    ITALY = ("italy", "it")
    JAPAN = ("japan", "jp")
    KUWAIT = ("kuwait", "kw")
    LATVIA = ("latvia", "lv")
    LITHUANIA = ("lithuania", "lt")
    LUXEMBOURG = ("luxembourg", "lu")
    MALAYSIA = ("malaysia", "malaysia:my")
    MALTA = ("malta", "malta:mt")
    MEXICO = ("mexico", "mx")
    MOROCCO = ("morocco", "ma")
    NETHERLANDS = ("netherlands", "nl")
    NEWZEALAND = ("new zealand", "nz")
    NIGERIA = ("nigeria", "ng")
    NORWAY = ("norway", "no")
    OMAN = ("oman", "om")
    PAKISTAN = ("pakistan", "pk")
    PANAMA = ("panama", "pa")
    PERU = ("peru", "pe")
    PHILIPPINES = ("philippines", "ph")
    POLAND = ("poland", "pl")
    PORTUGAL = ("portugal", "pt")
    QATAR = ("qatar", "qa")
    ROMANIA = ("romania", "ro")
    SAUDIARABIA = ("saudi arabia", "sa")
    SINGAPORE = ("singapore", "sg")
    SLOVAKIA = ("slovakia", "sk")
    SLOVENIA = ("slovenia", "sl")
    SOUTHAFRICA = ("south africa", "za")
    SOUTHKOREA = ("south korea", "kr")
    SPAIN = ("spain", "es")
    SWEDEN = ("sweden", "se")
    SWITZERLAND = ("switzerland", "ch")
    TAIWAN = ("taiwan", "tw")
    THAILAND = ("thailand", "th")
    TURKEY = ("türkiye,turkey", "tr")
    UKRAINE = ("ukraine", "ua")
    UNITEDARABEMIRATES = ("united arab emirates", "ae")
    UK = ("uk,united kingdom", "uk:gb")
    USA = ("usa,us,united states", "www:us")
    URUGUAY = ("uruguay", "uy")
    VENEZUELA = ("venezuela", "ve")
    VIETNAM = ("vietnam", "vn")

    # internal for linkedin
    WORLDWIDE = ("worldwide", "www")

    @property
    def indeed_domain_value(self):
        subdomain, _, api_country_code = self.value[1].partition(":")
        if subdomain and api_country_code:
            return subdomain, api_country_code.upper()
        return self.value[1], self.value[1].upper()

    @classmethod
    def from_string(cls, country_str: str):
        """Convert a string to the corresponding Country enum."""
        country_str = country_str.strip().lower()
        for country in cls:
            country_names = country.value[0].split(",")
            if country_str in country_names:
                return country
        valid_countries = [country.value for country in cls]
        raise ValueError(
            f"Invalid country string: '{country_str}'. Valid countries are: {', '.join([country[0] for country in valid_countries])}"
        )


class Location(BaseModel):
    country: Country | str | None = None
    city: Optional[str] = None
    state: Optional[str] = None

    def display_location(self) -> str:
        location_parts = []
        if self.city:
            location_parts.append(self.city)
        if self.state:
            location_parts.append(self.state)
        if isinstance(self.country, str):
            location_parts.append(self.country)
        elif self.country and self.country != Country.WORLDWIDE:
            country_name = self.country.value[0]
            if "," in country_name:
                country_name = country_name.split(",")[0]
            if country_name in ("usa", "uk"):
                location_parts.append(country_name.upper())
            else:
                location_parts.append(country_name.title())
        return ", ".join(location_parts)


class CompensationInterval(Enum):
    YEARLY = "yearly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    HOURLY = "hourly"

    @classmethod
    def get_interval(cls, pay_period):
        interval_mapping = {
            "YEAR": cls.YEARLY,
            "HOUR": cls.HOURLY,
        }
        if pay_period in interval_mapping:
            return interval_mapping[pay_period].value
        else:
            return cls[pay_period].value if pay_period in cls.__members__ else None


class Compensation(BaseModel):
    interval: Optional[CompensationInterval] = None
    min_amount: float | None = None
    max_amount: float | None = None
    currency: Optional[str] = "USD"


class DescriptionFormat(Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    PLAIN = "plain"

class JobPost(BaseModel):
    id: str | None = None
    title: str
    company_name: str | None
    job_url: str
    job_url_direct: str | None = None
    location: Optional[Location]

    description: str | None = None
    company_url: str | None = None
    company_url_direct: str | None = None

    job_type: list[JobType] | None = None
    compensation: Compensation | None = None
    date_posted: date | None = None
    emails: list[str] | None = None
    is_remote: bool | None = None
    listing_type: str | None = None

    # LinkedIn specific
    job_level: str | None = None

    # LinkedIn and Indeed specific
    company_industry: str | None = None

    # Indeed specific
    company_addresses: str | None = None
    company_num_employees: str | None = None
    company_revenue: str | None = None
    company_description: str | None = None
    company_logo: str | None = None
    banner_photo_url: str | None = None

    # LinkedIn only atm
    job_function: str | None = None

    # Optional source metadata
    skills: list[str] | None = None  #from tagsAndSkills
    experience_range: str | None = None  #from experienceText
    company_rating: float | None = None  #from ambitionBoxData.AggregateRating
    company_reviews_count: int | None = None  #from ambitionBoxData.ReviewsCount
    vacancy_count: int | None = None  #from vacancy
    work_from_home_type: str | None = None  #from clusters.wfhType (e.g., "Hybrid", "Remote")

class JobResponse(BaseModel):
    jobs: list[JobPost] = []


class Site(Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"


class SalarySource(Enum):
    DIRECT_DATA = "direct_data"
    DESCRIPTION = "description"


class ScraperInput(BaseModel):
    site_type: list[Site]
    search_term: str | None = None

    location: str | None = None
    country: Country | None = Country.USA
    distance: int | None = None
    is_remote: bool = False
    job_type: JobType | None = None
    easy_apply: bool | None = None
    offset: int = 0
    linkedin_fetch_description: bool = False
    linkedin_company_ids: list[int] | None = None
    description_format: DescriptionFormat | None = DescriptionFormat.MARKDOWN

    request_timeout: int = 60

    results_wanted: int = 15
    hours_old: int | None = None


class Scraper(ABC):
    def __init__(
        self, site: Site, proxies: list[str] | None = None, ca_cert: str | None = None, user_agent: str | None = None
    ):
        self.site = site
        self.proxies = proxies
        self.ca_cert = ca_cert
        self.user_agent = user_agent

    @abstractmethod
    def scrape(self, scraper_input: ScraperInput) -> JobResponse: ...
