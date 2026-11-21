from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Optional
from urllib.parse import urlparse, urlunparse, unquote

import regex as re
from bs4 import BeautifulSoup
from bs4.element import Tag

from jobspy.exception import LinkedInException, LinkedInJobGone, LinkedInRateLimitException, PaginationLimit
from jobspy.linkedin.constant import headers
from jobspy.linkedin.request import request_gate
from jobspy.linkedin.util import (
    is_job_remote,
    job_type_code,
    parse_job_type,
    parse_job_level,
    parse_company_industry
)
from jobspy.model import (
    JobPost,
    Location,
    JobResponse,
    Country,
    Compensation,
    DescriptionFormat,
    Scraper,
    ScraperInput,
    Site,
)
from jobspy.util import (
    extract_emails_from_text,
    currency_parser,
    markdown_converter,
    plain_converter,
    create_session,
    remove_attributes,
    create_logger,
)

log = create_logger("LinkedIn")


class LinkedIn(Scraper):
    base_url = "https://www.linkedin.com"
    jobs_per_page = 25

    def __init__(
        self, proxies: list[str] | str | None = None, ca_cert: str | None = None, user_agent: str | None = None
    ):
        """
        Initializes LinkedInScraper with the LinkedIn job search url
        """
        super().__init__(Site.LINKEDIN, proxies=proxies, ca_cert=ca_cert)
        self.session = create_session(
            proxies=self.proxies,
            ca_cert=ca_cert,
            # Shared request_gate owns 429 retries; avoid nested HTTP retries.
            has_retry=False,
            clear_cookies=True,
        )
        self.session.headers.update(headers)
        self.scraper_input = None
        self.country = "worldwide"
        self.job_url_direct_regex = re.compile(r'(?<=\?url=)[^"]+')

    def scrape(self, scraper_input: ScraperInput) -> JobResponse:
        start = scraper_input.offset or 0
        jobs = {}
        while len(jobs) < scraper_input.results_wanted and start < 1000:
            page, following = self.scrape_page(
                scraper_input, str(start), seen_ids=jobs.keys(),
                limit=scraper_input.results_wanted - len(jobs),
            )
            for job in page:
                # The standalone JobSpy API exposes numeric salary columns.
                job.compensation = self._bulk_compensation(job.salary_label)
                identity = job.id.removeprefix("li-") if job.id else job.job_url
                jobs[identity] = job
            if following is None:
                break
            start = int(following)
        return JobResponse(jobs=list(jobs.values()))

    def scrape_page(self, scraper_input, cursor=None, *, seen_ids=(), limit=None):
        """Read one native list response. Advance by raw cards, before ID or country filtering."""
        self.scraper_input = scraper_input
        start = int(cursor or 0)
        if start >= 1000:
            raise PaginationLimit("LinkedIn reached offset 1000 without a confirmed last page")
        seconds_old = scraper_input.hours_old * 3600 if scraper_input.hours_old else None
        if scraper_input.posted_since is not None:
            seconds_old = max(1, ceil((datetime.now(timezone.utc) - scraper_input.posted_since).total_seconds()))
        params = {
            "keywords": scraper_input.search_term,
            "location": scraper_input.location,
            "distance": scraper_input.distance,
            "f_WT": 2 if scraper_input.is_remote else None,
            "f_JT": (
                job_type_code(scraper_input.job_type)
                if scraper_input.job_type
                else None
            ),
            "pageNum": 0,
            "start": start,
            "f_AL": "true" if scraper_input.easy_apply else None,
            "f_C": (
                ",".join(map(str, scraper_input.linkedin_company_ids))
                if scraper_input.linkedin_company_ids
                else None
            ),
        }
        if seconds_old is not None:
            params["f_TPR"] = f"r{seconds_old}"

        params = {k: v for k, v in params.items() if v is not None}
        response = request_gate.get(
            self.session, f"{self.base_url}/jobs-guest/jobs/api/seeMoreJobPostings/search",
            params=params, timeout=10,
        )
        response.raise_for_status()
        if self._sign_in_page(response):
            raise LinkedInException("LinkedIn returned a sign-in page during search")
        if not response.text.strip():
            return [], None
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("div.base-search-card")
        if not cards:
            elements = soup.find_all(True, limit=2)
            empty_list = (len(elements) == 1 and elements[0].name == "ul"
                          and not soup.get_text(strip=True))
            if empty_list or soup.select_one(".no-results, .jobs-search-no-results-banner"):
                return [], None
            raise LinkedInException("Unrecognized LinkedIn search response; end of results unconfirmed")
        jobs = []
        seen = set(seen_ids)
        for card in cards:
            link = card.select_one("a.base-card__full-link[href]")
            path = urlparse(link["href"]).path.rstrip("/") if link else ""
            urn = re.fullmatch(r"urn:li:jobPosting:(\d+)", card.get("data-entity-urn", ""))
            if not urn and (not path.startswith("/jobs/view/") or not path.rsplit("/", 1)[-1]):
                raise LinkedInException("Unrecognized LinkedIn posting link")
            suffix = re.search(r"(?:^|-)(\d+)$", path.rsplit("/", 1)[-1])
            match = urn or suffix
            job_id = match[1] if match else None
            posting_path = f"/jobs/view/{job_id}" if job_id else path
            identity = job_id or f"{self.base_url}{posting_path}"
            # The campaign compares all card variants and keeps the more complete record.
            if limit is not None and identity in seen:
                continue
            seen.add(identity)
            jobs.append(self._process_job(
                card, job_id, scraper_input.linkedin_fetch_description, posting_path=posting_path
            ))
            if limit is not None and len(jobs) >= limit:
                break
        return jobs, str(start + len(cards))

    @staticmethod
    def _sign_in_page(response):
        path = urlparse(response.url).path
        return any(part in path for part in ("/login", "/signup", "/authwall", "/checkpoint"))

    @staticmethod
    def _bulk_compensation(label: str | None) -> Compensation | None:
        if not label:
            return None
        parts = re.split(r"[-–—]", label, maxsplit=1)
        try:
            minimum = currency_parser(parts[0])
            maximum = currency_parser(parts[1]) if len(parts) == 2 else None
        except ValueError:
            return None
        return Compensation(
            min_amount=minimum, max_amount=maximum, currency="USD" if label[0] == "$" else label[0]
        )

    def _process_job(
        self, job_card: Tag, job_id: str | None, full_descr: bool, *, posting_path: str
    ) -> Optional[JobPost]:
        salary_tag = job_card.find("span", class_="job-search-card__salary-info")

        salary_label = salary_tag.get_text(" ", strip=True) if salary_tag else None
        description = None

        title_tag = job_card.find("span", class_="sr-only")
        title = title_tag.get_text(strip=True) if title_tag else "N/A"

        company_tag = job_card.find("h4", class_="base-search-card__subtitle")
        company_a_tag = company_tag.find("a") if company_tag else None
        company_url = (
            urlunparse(urlparse(company_a_tag.get("href"))._replace(query=""))
            if company_a_tag and company_a_tag.has_attr("href")
            else ""
        )
        company = company_a_tag.get_text(strip=True) if company_a_tag else "N/A"

        metadata_card = job_card.find("div", class_="base-search-card__metadata")
        location = self._get_location(metadata_card)

        datetime_tag = (
            metadata_card.find("time", class_="job-search-card__listdate")
            if metadata_card
            else None
        )
        if not datetime_tag and metadata_card:
            datetime_tag = metadata_card.find(
                "time", class_="job-search-card__listdate--new"
            )
        date_posted = None
        if datetime_tag and "datetime" in datetime_tag.attrs:
            datetime_str = datetime_tag["datetime"]
            try:
                date_posted = datetime.strptime(datetime_str, "%Y-%m-%d")
            except:
                date_posted = None
        job_details = {}
        if full_descr:
            try:
                job_details = self._get_job_details(job_id or posting_path)
            except LinkedInRateLimitException:
                raise
            except Exception:
                # The standalone bulk API has always kept the card if its details fail.
                job_details = {}
            description = job_details.get("description")
        is_remote = is_job_remote(title, description, location)

        return JobPost(
            id=f"li-{job_id}" if job_id else None,
            title=title,
            company_name=company,
            company_url=company_url,
            location=location,
            is_remote=is_remote,
            date_posted=date_posted,
            job_url=f"{self.base_url}{posting_path}",
            salary_label=salary_label or None,
            job_type=job_details.get("job_type"),
            job_level=(job_details.get("job_level") or "").lower(),
            company_industry=job_details.get("company_industry"),
            description=job_details.get("description"),
            job_url_direct=job_details.get("job_url_direct"),
            emails=extract_emails_from_text(description),
            company_logo=job_details.get("company_logo"),
            job_function=job_details.get("job_function"),
        )

    def get_job_details(
        self,
        job_id: str,
        description_format: DescriptionFormat = DescriptionFormat.MARKDOWN,
    ) -> dict:
        """Fetch one job detail page without repeating a keyword search."""

        return self._get_job_details(job_id, description_format=description_format)

    def _get_job_details(
        self,
        job_id: str,
        *,
        description_format: DescriptionFormat | None = None,
    ) -> dict:
        """
        Retrieves job description and other job details by going to the job page url
        :param job_page_url:
        :return: dict
        """
        path = job_id if job_id.startswith("/jobs/view/") else f"/jobs/view/{job_id}"
        response = request_gate.get(self.session, f"{self.base_url}{path}", timeout=5)
        if self._sign_in_page(response):
            raise LinkedInException("LinkedIn returned a sign-in page for the job")
        if response.status_code in (404, 410):
            raise LinkedInJobGone(f"LinkedIn job {job_id} returned HTTP {response.status_code}")
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        div_content = soup.find(
            "div", class_=lambda x: x and "show-more-less-html__markup" in x
        )
        description = None
        if div_content is not None:
            div_content = remove_attributes(div_content)
            description = div_content.prettify(formatter="html")
            output_format = description_format or self.scraper_input.description_format
            if output_format == DescriptionFormat.MARKDOWN:
                description = markdown_converter(description)
            elif output_format == DescriptionFormat.PLAIN:
                description = plain_converter(description)
        h3_tag = soup.find(
            "h3", string=lambda text: text and "Job function" in text.strip()
        )

        job_function = None
        if h3_tag:
            job_function_span = h3_tag.find_next(
                "span", class_="description__job-criteria-text"
            )
            if job_function_span:
                job_function = job_function_span.text.strip()

        company_logo = (
            logo_image.get("data-delayed-url")
            if (logo_image := soup.find("img", {"class": "artdeco-entity-image"}))
            else None
        )
        return {
            "description": description,
            "job_level": parse_job_level(soup),
            "company_industry": parse_company_industry(soup),
            "job_type": parse_job_type(soup),
            "job_url_direct": self._parse_job_url_direct(soup),
            "company_logo": company_logo,
            "job_function": job_function,
        }

    def _get_location(self, metadata_card: Optional[Tag]) -> Location:
        """
        Extracts the location data from the job metadata card.
        :param metadata_card
        :return: location
        """
        tag = metadata_card.select_one(".job-search-card__location") if metadata_card else None
        parts = [part.strip() for part in tag.get_text(strip=True).split(",")] if tag else []
        if not parts:
            return Location()
        try:
            country = Country.from_string(parts[-1])
        except ValueError:
            # Keep the provider's wording for city/region-only or unknown locations.
            return Location(city=parts[0], state=", ".join(parts[1:]) or None)
        return Location(country=country, city=parts[0] if len(parts) > 1 else None,
                        state=", ".join(parts[1:-1]) or None)

    def _parse_job_url_direct(self, soup: BeautifulSoup) -> str | None:
        """
        Gets the job url direct from job page
        :param soup:
        :return: str
        """
        job_url_direct = None
        job_url_direct_content = soup.find("code", id="applyUrl")
        if job_url_direct_content:
            job_url_direct_match = self.job_url_direct_regex.search(
                job_url_direct_content.decode_contents().strip()
            )
            if job_url_direct_match:
                job_url_direct = unquote(job_url_direct_match.group())

        return job_url_direct
