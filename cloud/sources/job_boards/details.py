"""Fetch the job description behind an employer posting URL.

SimplifyJobs gap-fill jobs only carry a title, company, location and the
employer's URL. This module resolves that URL to a description:

1. Known ATS posting APIs (all public, no auth):
   Greenhouse (including pages that embed it via ``?gh_jid=``), Lever, Ashby,
   Workday (CXS), SmartRecruiters, Workable and BambooHR.
2. Otherwise the page's schema.org ``JobPosting`` JSON-LD, which career sites
   publish for Google for Jobs (iCIMS, Eightfold, many custom sites).

Pages that need a browser or block bots (Tesla, Oracle and UKG single-page
apps, ...) stay without a description; nothing here raises.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from cloud.sources.job_boards.boards import greenhouse_embed_candidates
from cloud.sources.job_boards.providers import html_to_markdown

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 WeCanFindIntern-job-collector/1.0"
)
MIN_HOST_INTERVAL_SECONDS = 0.3
MIN_DESCRIPTION_CHARS = 200

_GREENHOUSE = re.compile(
    r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)/jobs/(\d+)", re.I
)
_LEVER = re.compile(r"jobs\.(eu\.)?lever\.co/([^/?#]+)/([0-9a-f-]{36})", re.I)
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", re.I)
_WORKDAY = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([^/?#]+)"
    r"(/job/[^?#]+)",
    re.I,
)
_WORKDAY_SITE = re.compile(
    r"https?://(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[a-z]{2}/)?recruiting/([^/?#]+)/"
    r"([^/?#]+)(/job/[^?#]+)",
    re.I,
)
_SMARTRECRUITERS = re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.I)
_WORKABLE = re.compile(r"apply\.workable\.com/([^/?#]+)/j/([A-Za-z0-9]+)", re.I)
_BAMBOO = re.compile(r"https?://([a-z0-9-]+)\.bamboohr\.com/careers/(\d+)", re.I)
_JSON_LD = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I
)


class Http:
    """Thin GET wrapper so tests can inject responses: returns (status, text)."""

    def __init__(self, session: Any | None = None, timeout: float = 20.0):
        if session is None:
            import requests

            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT})
        self.session = session
        self.timeout = timeout

    def get(self, url: str, *, accept_json: bool = False) -> tuple[int, str]:
        headers = {"Accept": "application/json"} if accept_json else {}
        response = self.session.get(url, timeout=self.timeout, headers=headers)
        return response.status_code, response.text


def _json(http: Http, url: str) -> Any:
    status, text = http.get(url, accept_json=True)
    if status != 200:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _markdown(value: str | None) -> str | None:
    text = html_to_markdown(html.unescape(value)) if value else None
    return text if text and len(text) >= 40 else None


def _join(*parts: str | None) -> str | None:
    text = "\n\n".join(part for part in parts if part)
    return text or None


# --------------------------------------------------------------------------- ATS APIs


def _greenhouse(http: Http, token: str, job_id: str) -> str | None:
    data = _json(http, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}")
    return _markdown((data or {}).get("content"))


def _greenhouse_embed(http: Http, url: str) -> str | None:
    parsed = greenhouse_embed_candidates(url)
    if parsed is None:
        return None
    job_id, candidates = parsed
    for token in candidates[:4]:
        description = _greenhouse(http, token, job_id)
        if description:
            return description
    return None


def _lever(http: Http, eu: str | None, token: str, posting: str) -> str | None:
    host = "api.eu.lever.co" if eu else "api.lever.co"
    data = _json(http, f"https://{host}/v0/postings/{token}/{posting}")
    if not data:
        return None
    lists = [
        f"{block.get('text', '')}\n{html_to_markdown(block.get('content')) or ''}"
        for block in data.get("lists") or []
    ]
    return _join(data.get("descriptionPlain"), *lists, data.get("additionalPlain"))


class _AshbyBoards:
    """Ashby has no per-posting endpoint: fetch each board once and index it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.boards: dict[str, dict[str, str]] = {}

    def description(self, http: Http, token: str, posting: str) -> str | None:
        key = token.casefold()
        with self.lock:
            cached = self.boards.get(key)
        if cached is None:
            data = _json(http, f"https://api.ashbyhq.com/posting-api/job-board/{token}") or {}
            cached = {
                str(job.get("id")): job.get("descriptionHtml") or job.get("descriptionPlain") or ""
                for job in data.get("jobs") or []
            }
            with self.lock:
                self.boards[key] = cached
        return _markdown(cached.get(posting))


def _workday(http: Http, host: str, tenant: str, site: str, path: str) -> str | None:
    data = _json(http, f"https://{host}/wday/cxs/{tenant}/{site}{path}")
    return _markdown(((data or {}).get("jobPostingInfo") or {}).get("jobDescription"))


def _smartrecruiters(http: Http, company: str, posting: str) -> str | None:
    data = _json(http, f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting}")
    sections = ((data or {}).get("jobAd") or {}).get("sections") or {}
    order = ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
    return _join(*(_markdown((sections.get(name) or {}).get("text")) for name in order))


def _workable(http: Http, account: str, shortcode: str) -> str | None:
    data = _json(http, f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}")
    if not data:
        return None
    return _join(
        *(_markdown(data.get(name)) for name in ("description", "requirements", "benefits"))
    )


def _bamboo(http: Http, company: str, job_id: str) -> str | None:
    data = _json(http, f"https://{company}.bamboohr.com/careers/{job_id}/detail")
    opening = ((data or {}).get("result") or {}).get("jobOpening") or {}
    return _markdown(opening.get("description"))


# --------------------------------------------------------------------------- JSON-LD


def _job_postings(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _job_postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            yield node
        for key in ("@graph", "mainEntity"):
            if key in node:
                yield from _job_postings(node[key])


def description_from_html(page: str) -> str | None:
    """The longest ``JobPosting.description`` in the page's JSON-LD blocks."""
    best = None
    for block in _JSON_LD.findall(page or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for posting in _job_postings(data):
            text = _markdown(posting.get("description"))
            if text and (best is None or len(text) > len(best)):
                best = text
    return best


def _json_ld(http: Http, url: str) -> str | None:
    status, text = http.get(url)
    return description_from_html(text) if status == 200 else None


# --------------------------------------------------------------------------- dispatch


def resolve(url: str) -> tuple[str, Callable[[Http, _AshbyBoards], str | None]]:
    """(method name, fetcher) for a posting URL."""
    if m := _GREENHOUSE.search(url):
        return "greenhouse", lambda http, _: _greenhouse(http, m[1], m[2])
    if "gh_jid=" in url.lower():
        return (
            "greenhouse_embed",
            lambda http, _: _greenhouse_embed(http, url) or _json_ld(http, url),
        )
    if m := _LEVER.search(url):
        return "lever", lambda http, _: _lever(http, m[1], m[2], m[3])
    if m := _ASHBY.search(url):
        return "ashby", lambda http, ashby: ashby.description(http, m[1], m[2])
    if m := _WORKDAY.match(url):
        host = f"{m[1]}.{m[2]}.myworkdayjobs.com"
        return "workday", lambda http, _: _workday(http, host, m[1], m[3], m[4])
    if m := _WORKDAY_SITE.match(url):
        host = f"{m[1]}.myworkdaysite.com"
        return "workday", lambda http, _: _workday(http, host, m[2], m[3], m[4])
    if m := _SMARTRECRUITERS.search(url):
        return "smartrecruiters", lambda http, _: _smartrecruiters(http, m[1], m[2])
    if m := _WORKABLE.search(url):
        return "workable", lambda http, _: _workable(http, m[1], m[2])
    if m := _BAMBOO.match(url):
        return "bamboohr", lambda http, _: _bamboo(http, m[1], m[2])
    return "json_ld", lambda http, _: _json_ld(http, url)


@dataclass
class DescriptionResult:
    descriptions: dict[str, str]  # url -> markdown description
    stats: Counter[str]


def fetch_descriptions(
    urls: list[str],
    *,
    budget_seconds: float = 600.0,
    concurrency: int = 8,
    http_factory: Callable[[], Http] = Http,
    clock: Callable[[], float] = time.monotonic,
) -> DescriptionResult:
    """Fetch descriptions for many URLs: one sequential queue per host, all within a budget.

    Failures (blocked pages, missing JSON-LD, timeouts) are counted, never raised.
    """
    deadline = clock() + budget_seconds
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in dict.fromkeys(urls):
        by_host[urlsplit(url).netloc.lower()].append(url)
    stats: Counter[str] = Counter()
    descriptions: dict[str, str] = {}
    lock = threading.Lock()
    ashby = _AshbyBoards()
    local = threading.local()

    def run_host(host_urls: list[str]) -> None:
        if not hasattr(local, "http"):
            local.http = http_factory()
        last = 0.0
        for url in host_urls:
            method, fetcher = resolve(url)
            if clock() > deadline:
                with lock:
                    stats["skipped_deadline"] += 1
                continue
            wait = last + MIN_HOST_INTERVAL_SECONDS - clock()
            if wait > 0:
                time.sleep(wait)
            last = clock()
            try:
                text = fetcher(local.http, ashby)
            except Exception:
                text = None
            with lock:
                if text and len(text) >= MIN_DESCRIPTION_CHARS:
                    descriptions[url] = text
                    stats[f"found_{method}"] += 1
                else:
                    stats[f"missing_{method}"] += 1

    hosts = sorted(by_host.values(), key=len, reverse=True)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(run_host, hosts))
    stats["found"] = len(descriptions)
    stats["requested"] = sum(len(items) for items in hosts)
    return DescriptionResult(descriptions, stats)
