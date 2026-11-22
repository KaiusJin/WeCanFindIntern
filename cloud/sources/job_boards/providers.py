"""Greenhouse, Lever and Ashby board clients that emit ``NormalizedJob`` records."""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from cloud.sources.job_boards.boards import Board
from cloud.sources.job_boards.relevance import (
    INTERN_TITLE,
    countries_in,
    pick_location,
    title_kind,
)
from wecanfindintern.domain.normalized_job import NormalizedJob, build_source_fingerprint


class HttpGet(Protocol):
    def __call__(self, url: str, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        """GET (or POST ``json``); decoded JSON, or None when the board does not exist."""


@dataclass
class Scope:
    countries: frozenset[str] = frozenset({"US", "CA"})
    include_new_grad: bool = False
    deadline: float | None = None  # clock() value; long boards stop early
    clock: Callable[[], float] = time.monotonic

    def expired(self) -> bool:
        return self.deadline is not None and self.clock() > self.deadline


@dataclass
class BoardResult:
    jobs: list[NormalizedJob]
    listed: int  # postings on the board before filtering
    exists: bool = True
    truncated: bool = False  # stopped at the deadline; the rest is polled next run


def html_to_markdown(value: str | None) -> str | None:
    if not value:
        return None
    try:
        from markdownify import markdownify
    except ImportError:  # pragma: no cover - markdownify ships with the collector deps
        text = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"[ \t]+", " ", html.unescape(text)).strip() or None
    return markdownify(value).strip() or None


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC).date()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _employment(kind: str, provider_value: str | None) -> list[str]:
    value = (provider_value or "").replace("-", "").replace(" ", "").casefold()
    mapped = {
        "intern": "internship",
        "internship": "internship",
        "fulltime": "fulltime",
        "parttime": "parttime",
        "contract": "contract",
        "temporary": "temporary",
    }.get(value)
    if kind == "internship":
        return ["internship"]
    return [mapped] if mapped else ["fulltime"]


def _job(
    board: Board,
    *,
    job_id: str,
    url: str,
    title: str,
    company: str | None,
    locations: list[str | None],
    hint_countries: set[str],
    kind: str,
    employment: str | None,
    posted: Any,
    description: str | None,
    is_remote: bool | None,
    raw: dict[str, Any],
    scope: Scope,
) -> NormalizedJob | None:
    countries = set(hint_countries)
    for text in locations:
        countries |= countries_in(text)
    countries &= scope.countries
    if not countries:
        return None
    source = board.provider
    return NormalizedJob(
        source_fingerprint=build_source_fingerprint(source, job_id, url),
        source=source,
        source_job_id=job_id,
        source_url=url,
        direct_url=url,
        title=title.strip(),
        company_name=company or board.company or board.token,
        location_text=pick_location(locations, countries),
        date_posted=_date(posted),
        employment_types=_employment(kind, employment),
        is_remote=is_remote,
        description=description,
        raw={"ats_board": board.token, "ats_kind": kind, **raw},
    )


# --------------------------------------------------------------------------- Greenhouse

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch_greenhouse(board: Board, get: HttpGet, scope: Scope) -> BoardResult:
    """List without content (small), then fetch content only for in-scope titles."""
    payload = get(GREENHOUSE_API.format(token=board.token))
    if payload is None:
        return BoardResult([], 0, exists=False)
    listed = payload.get("jobs") or []
    jobs = []
    for item in listed:
        kind = title_kind(item.get("title") or "", include_new_grad=scope.include_new_grad)
        if kind is None:
            continue
        offices = [o.get("location") or o.get("name") for o in item.get("offices") or []]
        location = (item.get("location") or {}).get("name")
        hints = countries_in(location)
        for office in offices:
            hints |= countries_in(office)
        if not hints & scope.countries:
            continue
        detail = get(f"{GREENHOUSE_API.format(token=board.token)}/{item['id']}") or {}
        job = _job(
            board,
            job_id=str(item["id"]),
            url=item.get("absolute_url") or "",
            title=item.get("title") or "",
            company=item.get("company_name") or detail.get("company_name"),
            locations=[location, *offices],
            hint_countries=hints,
            kind=kind,
            employment=None,
            posted=item.get("first_published") or item.get("updated_at"),
            description=html_to_markdown(html.unescape(detail.get("content") or "")),
            is_remote="remote" in (location or "").casefold() or None,
            raw={"ats_updated_at": item.get("updated_at")},
            scope=scope,
        )
        if job is not None and job.source_url:
            jobs.append(job)
    return BoardResult(jobs, len(listed))


# --------------------------------------------------------------------------- Lever

LEVER_API = "https://api.lever.co/v0/postings/{token}"


def fetch_lever(board: Board, get: HttpGet, scope: Scope) -> BoardResult:
    payload = get(LEVER_API.format(token=board.token), {"mode": "json"})
    if payload is None or not isinstance(payload, list):
        return BoardResult([], 0, exists=payload is not None)
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        commitment = categories.get("commitment")
        kind = title_kind(
            item.get("text") or "",
            include_new_grad=scope.include_new_grad,
            intern_hint="intern" in (commitment or "").casefold(),
        )
        if kind is None:
            continue
        country = (item.get("country") or "").upper()
        locations = [categories.get("location"), *(categories.get("allLocations") or [])]
        sections = [item.get("descriptionPlain") or ""]
        for block in item.get("lists") or []:
            sections.append(
                f"{block.get('text', '')}\n{html_to_markdown(block.get('content')) or ''}"
            )
        sections.append(item.get("additionalPlain") or "")
        job = _job(
            board,
            job_id=str(item["id"]),
            url=item.get("hostedUrl") or "",
            title=item.get("text") or "",
            company=None,
            locations=locations,
            hint_countries={country} if country else set(),
            kind=kind,
            employment=commitment,
            posted=item.get("createdAt"),
            description="\n\n".join(s.strip() for s in sections if s and s.strip()) or None,
            is_remote=(item.get("workplaceType") == "remote") or None,
            raw={"ats_team": categories.get("team")},
            scope=scope,
        )
        if job is not None and job.source_url:
            jobs.append(job)
    return BoardResult(jobs, len(payload))


# --------------------------------------------------------------------------- Ashby

ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def _ashby_address(address: dict | None) -> tuple[str | None, str | None]:
    postal = (address or {}).get("postalAddress") or {}
    parts = [
        postal.get("addressLocality"),
        postal.get("addressRegion"),
        postal.get("addressCountry"),
    ]
    text = ", ".join(p for p in parts if p) or None
    return text, postal.get("addressCountry")


def fetch_ashby(board: Board, get: HttpGet, scope: Scope) -> BoardResult:
    payload = get(ASHBY_API.format(token=board.token))
    if payload is None:
        return BoardResult([], 0, exists=False)
    listed = payload.get("jobs") or []
    jobs = []
    for item in listed:
        if item.get("isListed") is False:
            continue
        kind = title_kind(
            item.get("title") or "",
            include_new_grad=scope.include_new_grad,
            intern_hint=item.get("employmentType") == "Intern",
        )
        if kind is None:
            continue
        locations: list[str | None] = []
        hints: set[str] = set()
        address_text, address_country = _ashby_address(item.get("address"))
        locations += [address_text, item.get("location")]
        hints |= countries_in(address_country)
        for secondary in item.get("secondaryLocations") or []:
            text, country = _ashby_address(secondary.get("address"))
            locations += [text, secondary.get("location")]
            hints |= countries_in(country)
        job = _job(
            board,
            job_id=str(item["id"]),
            url=item.get("jobUrl") or "",
            title=item.get("title") or "",
            company=None,
            locations=locations,
            hint_countries=hints,
            kind=kind,
            employment=item.get("employmentType"),
            posted=item.get("publishedAt"),
            description=html_to_markdown(item.get("descriptionHtml"))
            or item.get("descriptionPlain"),
            is_remote=bool(item.get("isRemote")) or None,
            raw={"ats_department": item.get("department")},
            scope=scope,
        )
        if job is not None and job.source_url:
            jobs.append(job)
    return BoardResult(jobs, len(listed))


# --------------------------------------------------------------------------- Workday

WORKDAY_SEARCHES = ("intern", "co-op")
WORKDAY_PAGE = 20  # the CXS API rejects larger pages
WORKDAY_MAX_PAGES = 25  # per search term
# Searches are fuzzy ("intern" also matches "International") but rank real hits
# first, so stop after this many consecutive pages without an intern-like title.
WORKDAY_BARREN_PAGES = 2
FOREIGN = re.compile(
    r"china|india|israel|taiwan|germany|japan|korea|singapore|united kingdom|\buk\b|ireland|"
    r"france|poland|mexico|brazil|philippines|vietnam|malaysia|netherlands|switzerland|spain|"
    r"italy|costa rica|romania|czech|hungary|australia|sweden|denmark|belgium|austria|"
    r"argentina|colombia|chile|portugal|turkey|egypt|south africa|indonesia|thailand",
    re.I,
)
_RELATIVE_POSTED = re.compile(r"posted (today|yesterday|(\d+)\+? days? ago)", re.I)


def _workday_urls(token: str) -> tuple[str, str]:
    host, tenant, site = token.split("/", 2)
    return f"https://{host}/wday/cxs/{tenant}/{site}", f"https://{host}/{site}"


def _workday_posted(info: dict[str, Any]) -> date | None:
    if info.get("startDate"):
        return _date(info["startDate"])
    match = _RELATIVE_POSTED.search(info.get("postedOn") or "")
    if not match:
        return None
    days = (
        0
        if match[1].lower() == "today"
        else 1
        if match[1].lower() == "yesterday"
        else int(match[2])
    )
    return date.fromordinal(datetime.now(UTC).date().toordinal() - days)


def fetch_workday(board: Board, get: HttpGet, scope: Scope) -> BoardResult:
    """Search the tenant's career site for intern/co-op postings, then fetch details
    only for postings whose title and listed location can be in scope."""
    api, public = _workday_urls(board.token)
    candidates: dict[str, dict[str, Any]] = {}
    listed = 0
    truncated = False
    for term in WORKDAY_SEARCHES:
        total = None
        barren = 0
        for page in range(WORKDAY_MAX_PAGES):
            if scope.expired():
                truncated = True
                break
            body = {
                "appliedFacets": {},
                "limit": WORKDAY_PAGE,
                "offset": page * WORKDAY_PAGE,
                "searchText": term,
            }
            payload = get(f"{api}/jobs", json=body)
            if payload is None:
                if page == 0 and term == WORKDAY_SEARCHES[0]:
                    return BoardResult([], 0, exists=False)
                break
            postings = payload.get("jobPostings") or []
            if total is None:
                total = int(payload.get("total") or 0)
            listed += len(postings)
            for posting in postings:
                path = posting.get("externalPath")
                if path:
                    candidates.setdefault(path, posting)
            hits = any(INTERN_TITLE.search(p.get("title") or "") for p in postings)
            barren = 0 if hits else barren + 1
            if not postings or (page + 1) * WORKDAY_PAGE >= total:
                break
            if barren >= WORKDAY_BARREN_PAGES:
                break
    jobs = []
    for path, posting in candidates.items():
        title = posting.get("title") or ""
        if title_kind(title, include_new_grad=scope.include_new_grad) is None:
            continue
        listed_location = posting.get("locationsText") or ""
        if FOREIGN.search(listed_location) and not countries_in(listed_location) & scope.countries:
            continue
        if scope.expired():
            truncated = True
            break
        detail = get(f"{api}{path}")
        info = (detail or {}).get("jobPostingInfo") or {}
        if not info:
            continue
        country = (info.get("country") or {}).get("descriptor")
        locations = [
            info.get("location"),
            *(info.get("additionalLocations") or []),
            listed_location,
        ]
        kind = title_kind(info.get("title") or title, include_new_grad=scope.include_new_grad)
        if kind is None:
            continue
        job = _job(
            board,
            # Requisition ids are only unique within a tenant (and shared by its sites).
            job_id=f"{board.token.split('/')[1]}:{info.get('jobReqId') or info.get('id') or path}",
            url=info.get("externalUrl") or f"{public}{path}",
            title=info.get("title") or title,
            company=None,
            locations=locations,
            hint_countries=countries_in(country),
            kind=kind,
            employment=None,
            posted=_workday_posted(info),
            description=html_to_markdown(info.get("jobDescription")),
            is_remote=info.get("remoteType") == "Remote" or None,
            raw={"ats_time_type": info.get("timeType")},
            scope=scope,
        )
        if job is not None:
            jobs.append(job)
    return BoardResult(jobs, listed, truncated=truncated)


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
}
