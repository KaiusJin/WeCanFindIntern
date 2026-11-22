"""SimplifyJobs community lists: board discovery, gap filling and coverage monitoring.

The SimplifyJobs internship and new-grad repositories publish a curated
``listings.json`` of US/Canada postings. Each run downloads it and uses it three
ways:

1. **Discovery.** Posting URLs on Greenhouse, Lever, Ashby and Workday add
   their boards to this run's poll list. Company pages that embed Greenhouse
   (``?gh_jid=``) are resolved to their board token.
2. **Gap filling.** Listings that no other source collected are emitted as
   ``source="simplify"`` jobs. They link to the employer's own posting.
3. **Coverage.** The active CS listings are the yardstick for the run report:
   recall per platform, plus a sample of misses.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from wecanfindintern.domain.normalized_job import NormalizedJob, build_source_fingerprint
from wecanfindintern.ingestion.ats.boards import (
    Board,
    board_from_url,
    greenhouse_embed_candidates,
    merge_boards,
)
from wecanfindintern.ingestion.ats.providers import Scope
from wecanfindintern.ingestion.ats.relevance import countries_in, pick_location, title_kind

LISTINGS = {
    "internships": "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "new_grad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
}
CS_CATEGORIES = frozenset(
    {
        "software",
        "ai/ml/data",
        "quant",
        "software engineering",
        "data science, ai & machine learning",
        "quantitative finance",
    }
)
PLATFORMS = ("greenhouse", "lever", "ashby", "workday")
_POSTING_ID = re.compile(
    r"gh_jid=(\d+)|/jobs?/(\d{5,})|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"|_((?:jr|r|req)[-_]?\d{4,}(?:-\d+)?)\b|/(\d{6,})(?:[/?#]|$)",
    re.I,
)
_STOP = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|the|group|technologies|technology|usa|us|na|labs?)\b"
)
_NOISE = re.compile(r"\b(20\d\d|summer|fall|winter|spring|intern(ship)?|co ?op|\d+)\b")


@dataclass
class SimplifyListing:
    list_name: str
    listing_id: str
    company: str
    title: str
    url: str
    locations: list[str]
    countries: set[str]
    kind: str
    date_posted: int | None
    category: str | None

    @property
    def platform(self) -> str:
        board = board_from_url(self.url)
        if board is not None:
            return board.provider
        return "greenhouse_embed" if "gh_jid=" in self.url.lower() else "other"


@dataclass
class SimplifySnapshot:
    listings: list[SimplifyListing]
    boards: list[Board]
    stats: Counter[str] = field(default_factory=Counter)


def posting_key(url: str | None) -> str | None:
    """A provider posting id found in a URL, comparable across sources."""
    if not url:
        return None
    match = _POSTING_ID.search(url)
    if not match:
        return None
    return next(group for group in match.groups() if group).lower()


def _company_key(name: str | None) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return " ".join(_STOP.sub(" ", text).split())


def _title_tokens(title: str | None) -> frozenset[str]:
    text = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    return frozenset(_NOISE.sub(" ", text).split())


def parse_listings(
    lists: dict[str, list[dict[str, Any]]], scope: Scope
) -> tuple[list[SimplifyListing], list[Board], Counter[str]]:
    """Active, visible, in-scope listings plus every board their URLs reveal."""
    stats: Counter[str] = Counter()
    listings: list[SimplifyListing] = []
    boards: list[Board] = []
    for list_name, rows in lists.items():
        internship_list = list_name == "internships"
        if not internship_list and not scope.include_new_grad:
            # New-grad postings still reveal boards that also post internships.
            boards.extend(
                b for row in rows if (b := board_from_url(row.get("url"), row.get("company_name")))
            )
            continue
        for row in rows:
            stats[f"{list_name}_rows"] += 1
            board = board_from_url(row.get("url"), row.get("company_name"))
            if board is not None:
                boards.append(board)
            if not row.get("active") or row.get("is_visible") is False:
                continue
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if not title or not url:
                continue
            category = row.get("category")
            kind = title_kind(
                title, include_new_grad=scope.include_new_grad, intern_hint=internship_list
            )
            if kind is None and str(category or "").casefold() in CS_CATEGORIES:
                kind = "internship" if internship_list else "new_grad"
            if kind is None:
                stats["out_of_scope_title"] += 1
                continue
            locations = [str(loc) for loc in row.get("locations") or []]
            countries = set()
            for location in locations:
                countries |= countries_in(location)
            countries &= set(scope.countries)
            if not countries:
                stats["out_of_scope_location"] += 1
                continue
            listings.append(
                SimplifyListing(
                    list_name=list_name,
                    listing_id=str(row.get("id") or url),
                    company=str(row.get("company_name") or "").strip(),
                    title=title,
                    url=url,
                    locations=locations,
                    countries=countries,
                    kind=kind,
                    date_posted=row.get("date_posted"),
                    category=category,
                )
            )
            stats["active_in_scope"] += 1
    return listings, merge_boards(boards), stats


def resolve_greenhouse_embeds(
    urls: Iterable[tuple[str, str | None]], get_status: Callable[[str], int]
) -> list[Board]:
    """Confirm the Greenhouse board behind company pages that embed it (``?gh_jid=``)."""
    found: dict[str, Board] = {}
    for url, company in urls:
        parsed = greenhouse_embed_candidates(url)
        if parsed is None:
            continue
        host = url.split("/")[2].lower() if url.count("/") >= 2 else url
        if host in found:
            continue
        job_id, candidates = parsed
        for token in candidates[:4]:
            api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
            if get_status(api) == 200:
                found[host] = Board("greenhouse", token, company or None)
                break
    return list(found.values())


class _Index:
    """Match a listing to collected jobs by posting id, else by company + title."""

    def __init__(self, jobs: Iterable[NormalizedJob]):
        self.keys: set[str] = set()
        self.titles: dict[str, list[frozenset[str]]] = defaultdict(list)
        for job in jobs:
            for url in (job.source_url, job.direct_url):
                key = posting_key(url)
                if key:
                    self.keys.add(key)
            self.titles[_company_key(job.company_name)].append(_title_tokens(job.title))

    def __contains__(self, listing: SimplifyListing) -> bool:
        key = posting_key(listing.url)
        if key and key in self.keys:
            return True
        tokens = _title_tokens(listing.title)
        company = _company_key(listing.company)
        candidates = self.titles.get(company, [])
        if not candidates and len(company) >= 4:
            candidates = next(
                (
                    v
                    for k, v in self.titles.items()
                    if len(k) >= 4 and (k.startswith(company) or company.startswith(k))
                ),
                [],
            )
        return any(
            tokens and other and len(tokens & other) / len(tokens | other) >= 0.6
            for other in candidates
        )


def coverage_report(
    listings: list[SimplifyListing], jobs: Iterable[NormalizedJob], *, sample: int = 15
) -> dict[str, Any]:
    """Recall of ``jobs`` against the SimplifyJobs reference, overall and per platform."""
    index = _Index(jobs)
    by_platform: dict[str, Counter[str]] = defaultdict(Counter)
    misses: list[dict[str, str]] = []
    for listing in listings:
        hit = listing in index
        bucket = by_platform[listing.platform]
        bucket["reference"] += 1
        bucket["covered"] += hit
        if not hit and len(misses) < sample:
            misses.append(
                {
                    "company": listing.company,
                    "title": listing.title,
                    "platform": listing.platform,
                    "url": listing.url,
                }
            )
    total = sum(c["reference"] for c in by_platform.values())
    covered = sum(c["covered"] for c in by_platform.values())
    return {
        "reference": total,
        "covered": covered,
        "recall": round(covered / total, 4) if total else None,
        "by_platform": {
            name: {
                "reference": c["reference"],
                "covered": c["covered"],
                "recall": round(c["covered"] / c["reference"], 4),
            }
            for name, c in sorted(by_platform.items(), key=lambda kv: -kv[1]["reference"])
        },
        "missed_sample": misses,
    }


def _posted(value: Any) -> Any:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, UTC).date()
    return None


def gap_fill_jobs(
    listings: list[SimplifyListing], collected: Iterable[NormalizedJob]
) -> list[NormalizedJob]:
    """Listings no other source collected, as ``simplify`` jobs pointing at the employer."""
    index = _Index(collected)
    jobs: dict[str, NormalizedJob] = {}
    for listing in listings:
        if listing in index:
            continue
        job = NormalizedJob(
            source_fingerprint=build_source_fingerprint(
                "simplify", listing.listing_id, listing.url
            ),
            source="simplify",
            source_job_id=listing.listing_id,
            source_url=listing.url,
            direct_url=listing.url,
            title=listing.title,
            company_name=listing.company or None,
            location_text=pick_location(listing.locations, listing.countries),
            date_posted=_posted(listing.date_posted),
            employment_types=["internship"] if listing.kind == "internship" else ["fulltime"],
            raw={
                "via": "simplify",
                "list": listing.list_name,
                "category": listing.category,
                "platform": listing.platform,
                "ats_kind": listing.kind,
            },
        )
        jobs[job.source_fingerprint] = job
    return sorted(jobs.values(), key=lambda job: job.source_fingerprint)


def fetch_simplify(
    scope: Scope,
    *,
    get_json: Callable[[str], Any] | None = None,
    get_status: Callable[[str], int] | None = None,
) -> SimplifySnapshot:
    if get_json is None or get_status is None:
        import requests

        session = requests.Session()
        session.headers["User-Agent"] = "WeCanFindIntern-job-collector/1.0"
        get_json = get_json or (lambda url: session.get(url, timeout=60).json())
        get_status = get_status or (lambda url: session.get(url, timeout=20).status_code)
    lists = {name: get_json(url) for name, url in LISTINGS.items()}
    listings, boards, stats = parse_listings(lists, scope)
    embedded = resolve_greenhouse_embeds(
        (
            (row.get("url") or "", row.get("company_name"))
            for rows in lists.values()
            for row in rows
            if row.get("active") and "gh_jid=" in str(row.get("url") or "").lower()
        ),
        get_status,
    )
    stats["embedded_greenhouse_boards"] = len(embedded)
    boards = merge_boards(boards, embedded)
    stats["boards_discovered"] = len(boards)
    return SimplifySnapshot(listings=listings, boards=boards, stats=stats)
