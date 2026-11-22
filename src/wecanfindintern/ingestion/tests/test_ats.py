from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from wecanfindintern.domain.location import parse_location
from wecanfindintern.ingestion.ats import Board, board_from_url, collect_ats_jobs, load_boards
from wecanfindintern.ingestion.ats.boards import boards_from_listings, save_boards
from wecanfindintern.ingestion.ats.providers import (
    Scope,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_workday,
)
from wecanfindintern.ingestion.ats.relevance import countries_in, pick_location, title_kind

# ----------------------------------------------------------------------------- boards


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://job-boards.greenhouse.io/stripe/jobs/123", ("greenhouse", "stripe")),
        ("https://boards.greenhouse.io/embed/job_app?for=figma&token=1", ("greenhouse", "figma")),
        ("https://jobs.lever.co/palantir/6ed7-abc/apply", ("lever", "palantir")),
        ("https://jobs.eu.lever.co/mistral/abc", ("lever", "mistral")),
        ("https://jobs.ashbyhq.com/openai/8fb1/application", ("ashby", "openai")),
        ("https://careers.example.com/jobs?gh_jid=1", None),
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/1",
            ("workday", "acme.wd5.myworkdayjobs.com/acme/External"),
        ),
        (
            "https://Intel.wd1.myworkdayjobs.com/External/job/Santa-Clara/x_R1",
            ("workday", "intel.wd1.myworkdayjobs.com/intel/External"),
        ),
        (
            "https://wd5.myworkdaysite.com/en-US/recruiting/nvidia/NVIDIASite/job/x",
            ("workday", "wd5.myworkdaysite.com/nvidia/NVIDIASite"),
        ),
        (None, None),
    ],
)
def test_board_from_url(url, expected):
    board = board_from_url(url)
    assert (board and (board.provider, board.token)) == expected


def test_registry_round_trip_merges_case_insensitively(tmp_path: Path):
    listings = [
        {"url": "https://jobs.ashbyhq.com/OpenAI/1", "company_name": "OpenAI"},
        {"url": "https://jobs.ashbyhq.com/openai/2", "company_name": ""},
        {"url": "https://jobs.lever.co/acme/3", "company_name": "Acme"},
        {"url": "https://example.com/careers", "company_name": "Nope"},
    ]
    path = tmp_path / "boards.json"
    save_boards(path, boards_from_listings(listings), note="test")
    boards = load_boards(path)
    assert [(b.provider, b.token, b.company) for b in boards] == [
        ("ashby", "OpenAI", "OpenAI"),
        ("lever", "acme", "Acme"),
    ]


def test_committed_registry_is_valid():
    path = Path(__file__).resolve().parents[4] / "scripts/collection/ats_boards.json"
    boards = load_boards(path)
    assert len(boards) > 100
    assert {b.provider for b in boards} == {"greenhouse", "lever", "ashby", "workday"}


# ----------------------------------------------------------------------------- relevance


@pytest.mark.parametrize(
    ("title", "new_grad", "expected"),
    [
        ("Software Engineer Intern (Summer 2027)", False, "internship"),
        ("Data Scientist Co-op", False, "internship"),
        ("Machine Learning Research Intern", False, "internship"),
        ("Mechanical Engineering Intern", False, None),
        ("Marketing Intern", False, None),
        ("New Market Launch Intern (MBA), AI", False, None),
        ("Senior Software Engineer", False, None),
        ("Software Engineer, New Grad", False, None),
        ("Software Engineer, New Grad", True, "new_grad"),
        ("Senior Software Engineer, Early Career Mentor", True, None),
        ("Internal Tools Engineer", False, None),
    ],
)
def test_title_kind(title, new_grad, expected):
    assert title_kind(title, include_new_grad=new_grad) == expected


def test_title_kind_uses_provider_intern_hint():
    assert title_kind("Backend Engineer", include_new_grad=False, intern_hint=True) == "internship"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Palo Alto, CA", {"US"}),
        ("Toronto, ON", {"CA"}),
        ("Seattle, San Francisco, New York City", {"US"}),
        ("New York, NY; Toronto, Ontario, Canada", {"US", "CA"}),
        ("London, UK", set()),
        ("Remote - US", {"US"}),
        ("Bangalore, India", set()),
    ],
)
def test_countries_in(text, expected):
    assert countries_in(text) == expected


@pytest.mark.parametrize(
    ("candidates", "countries", "country", "region"),
    [
        (["Palo Alto, CA"], {"US"}, "US", "CA"),
        (["San Francisco, CA USA"], {"US"}, "US", "CA"),
        (["London, UK; Toronto, ON"], {"CA"}, "CA", "ON"),
        (["Remote"], {"US"}, "US", None),
        (["US, CA, Santa Clara"], {"US"}, "US", "CA"),
    ],
)
def test_pick_location_is_unambiguous_for_shared_parser(candidates, countries, country, region):
    location = parse_location(pick_location(candidates, countries))
    assert (location.country_code, location.region_code) == (country, region)


# ----------------------------------------------------------------------------- providers


class FakeGet:
    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, params=None, json=None):
        self.calls.append(url)
        route = self.routes.get(url)
        if callable(route):
            return route(json)
        return route


def test_greenhouse_fetches_content_only_for_matches():
    base = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    get = FakeGet(
        {
            base: {
                "jobs": [
                    {
                        "id": 1,
                        "title": "Software Engineer Intern",
                        "absolute_url": "https://acme.com/j/1",
                        "location": {"name": "Palo Alto, CA"},
                        "first_published": "2026-11-01T10:00:00-04:00",
                        "company_name": "Acme",
                    },
                    {
                        "id": 2,
                        "title": "Account Executive",
                        "absolute_url": "https://acme.com/j/2",
                        "location": {"name": "Palo Alto, CA"},
                    },
                    {
                        "id": 3,
                        "title": "Software Engineer Intern",
                        "absolute_url": "https://acme.com/j/3",
                        "location": {"name": "London, UK"},
                        "offices": [{"name": "London"}],
                    },
                ]
            },
            f"{base}/1": {"content": "&lt;p&gt;Build &lt;b&gt;things&lt;/b&gt;&lt;/p&gt;"},
        }
    )
    result = fetch_greenhouse(Board("greenhouse", "acme"), get, Scope())
    assert result.listed == 3
    assert [job.source_job_id for job in result.jobs] == ["1"]
    job = result.jobs[0]
    assert get.calls == [base, f"{base}/1"]
    assert job.source == "greenhouse"
    assert job.company_name == "Acme"
    assert job.date_posted == date(2026, 11, 1)
    assert job.employment_types == ["internship"]
    assert "**things**" in job.description
    assert parse_location(job.location_text).country_code == "US"


def test_greenhouse_missing_board():
    assert fetch_greenhouse(Board("greenhouse", "gone"), FakeGet({}), Scope()).exists is False


def test_lever_uses_country_and_commitment():
    url = "https://api.lever.co/v0/postings/acme"
    get = FakeGet(
        {
            url: [
                {
                    "id": "a",
                    "text": "Backend Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/a",
                    "categories": {"commitment": "Intern", "location": "Waterloo"},
                    "country": "CA",
                    "createdAt": 1793000000000,
                    "descriptionPlain": "About",
                    "lists": [{"text": "You will", "content": "<li>ship</li>"}],
                },
                {
                    "id": "b",
                    "text": "Backend Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/b",
                    "categories": {"commitment": "Full-time", "location": "Waterloo"},
                    "country": "CA",
                },
            ]
        }
    )
    result = fetch_lever(Board("lever", "acme", "Acme Co"), get, Scope())
    assert [job.source_job_id for job in result.jobs] == ["a"]
    job = result.jobs[0]
    assert job.company_name == "Acme Co"
    assert job.employment_types == ["internship"]
    assert parse_location(job.location_text).country_code == "CA"
    assert "You will" in job.description and "ship" in job.description


def test_ashby_secondary_location_and_unlisted():
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    get = FakeGet(
        {
            url: {
                "jobs": [
                    {
                        "id": "x",
                        "title": "Data Engineer",
                        "employmentType": "Intern",
                        "isListed": True,
                        "jobUrl": "https://jobs.ashbyhq.com/acme/x",
                        "publishedAt": "2026-11-05T02:20:02.702+00:00",
                        "location": "London",
                        "address": {"postalAddress": {"addressCountry": "United Kingdom"}},
                        "secondaryLocations": [
                            {
                                "location": "Toronto",
                                "address": {
                                    "postalAddress": {
                                        "addressLocality": "Toronto",
                                        "addressRegion": "Ontario",
                                        "addressCountry": "Canada",
                                    }
                                },
                            }
                        ],
                        "descriptionHtml": "<p>Pipelines</p>",
                    },
                    {
                        "id": "y",
                        "title": "Data Engineer Intern",
                        "isListed": False,
                        "jobUrl": "https://jobs.ashbyhq.com/acme/y",
                        "location": "Toronto",
                    },
                ]
            }
        }
    )
    result = fetch_ashby(Board("ashby", "acme", "Acme"), get, Scope())
    assert [job.source_job_id for job in result.jobs] == ["x"]
    assert result.jobs[0].location_text == "Toronto, Ontario, Canada"
    assert result.jobs[0].description == "Pipelines"


# ----------------------------------------------------------------------------- collector


class FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, script: dict[str, list[FakeResponse]]):
        self.script = script

    def get(self, url, params=None, timeout=None):
        queue = self.script[url]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def post(self, url, params=None, json=None, timeout=None):
        return self.get(url, params=params, timeout=timeout)


def test_collector_retries_isolates_failures_and_dedupes():
    ok_board = "https://api.ashbyhq.com/posting-api/job-board/good"
    job = {
        "id": "1",
        "title": "Software Engineer Intern",
        "jobUrl": "https://jobs.ashbyhq.com/good/1",
        "location": "Toronto, ON",
    }
    script = {
        ok_board: [FakeResponse(429), FakeResponse(200, {"jobs": [job, job]})],
        "https://api.ashbyhq.com/posting-api/job-board/broken": [FakeResponse(403)],
        "https://api.ashbyhq.com/posting-api/job-board/gone": [FakeResponse(404)],
    }
    sleeps: list[float] = []
    result = collect_ats_jobs(
        [Board("ashby", "good"), Board("ashby", "broken"), Board("ashby", "gone")],
        concurrency=1,
        session_factory=lambda: FakeSession(script),
        sleep=sleeps.append,
    )
    assert [j.source_job_id for j in result.jobs] == ["1"]
    assert result.stats["boards_ok"] == 1
    assert result.stats["boards_failed"] == 1
    assert result.missing_boards == ["ashby:gone"]
    assert len(sleeps) == 1  # one backoff for the 429


def test_collector_gives_up_on_a_board_after_bounded_retries():
    url = "https://api.ashbyhq.com/posting-api/job-board/flaky"
    result = collect_ats_jobs(
        [Board("ashby", "flaky")],
        concurrency=1,
        session_factory=lambda: FakeSession({url: [FakeResponse(503)]}),
        sleep=lambda _: None,
    )
    assert result.stats["boards_failed"] == 1
    assert "gave up" in result.failed_boards[0]


def test_registry_file_shape(tmp_path: Path):
    path = tmp_path / "b.json"
    path.write_text(
        json.dumps(
            {
                "boards": [
                    {"provider": "smartrecruiters", "token": "x"},
                    {"provider": "lever", "token": " acme "},
                ]
            }
        )
    )
    assert load_boards(path) == [Board("lever", "acme", None)]


def test_workday_searches_pages_and_scopes_details():
    api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External"

    def search(body):
        if body["searchText"] == "co-op":
            return {"total": 0, "jobPostings": []}
        page = [
            {
                "title": "Software Engineer Intern",
                "externalPath": "/job/US-CA-Santa-Clara/SWE_R1",
                "locationsText": "US, CA, Santa Clara",
            },
            {
                "title": "GPU Compiler Intern",
                "externalPath": "/job/China-Shanghai/GPU_R2",
                "locationsText": "China, Shanghai",
            },
            {
                "title": "International Tax Director",
                "externalPath": "/job/US/Tax_R3",
                "locationsText": "US, CA, Santa Clara",
            },
        ]
        return {"total": 23, "jobPostings": page if body["offset"] == 0 else page[:1]}

    get = FakeGet(
        {
            f"{api}/jobs": search,
            f"{api}/job/US-CA-Santa-Clara/SWE_R1": {
                "jobPostingInfo": {
                    "title": "Software Engineer Intern",
                    "jobReqId": "R1",
                    "location": "US, CA, Santa Clara",
                    "country": {"descriptor": "United States of America"},
                    "startDate": "2026-11-02",
                    "jobDescription": "<p>Kernels</p>",
                    "externalUrl": "https://acme.wd5.myworkdayjobs.com/External/job/US-CA-Santa-Clara/SWE_R1",
                }
            },
        }
    )
    result = fetch_workday(
        Board("workday", "acme.wd5.myworkdayjobs.com/acme/External", "Acme"), get, Scope()
    )
    searches = [c for c in get.calls if c.endswith("/jobs")]
    assert len(searches) == 3  # two pages of "intern" (total 23), one of "co-op"
    assert not any("China" in c or "Tax" in c for c in get.calls)
    assert [job.source_job_id for job in result.jobs] == ["acme:R1"]
    job = result.jobs[0]
    assert job.source == "workday"
    assert job.company_name == "Acme"
    assert job.location_text == "Santa Clara, CA, United States"
    assert job.date_posted == date(2026, 11, 2)
    assert job.description == "Kernels"


def test_workday_missing_site():
    board = Board("workday", "gone.wd1.myworkdayjobs.com/gone/External")
    assert fetch_workday(board, FakeGet({}), Scope()).exists is False


def test_rate_limit_never_fails_a_board_only_defers_it_to_the_deadline():
    url = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    result = collect_ats_jobs(
        [Board("workday", "acme.wd3.myworkdayjobs.com/acme/External")],
        session_factory=lambda: FakeSession({url: [FakeResponse(429)]}),
        sleep=sleep,
        clock=lambda: now[0],
        deadline=1_000.0,
    )
    assert result.stats["boards_failed"] == 0
    assert result.stats["boards_skipped_deadline"] == 1
    assert result.stats["rate_limited"] >= 4  # walked the cooldown ladder, never gave up


def test_workday_pods_get_their_own_sequential_queue():
    from wecanfindintern.ingestion.ats.collector import host_key

    assert host_key(Board("workday", "acme.wd3.myworkdayjobs.com/acme/X")) == (
        "workday:wd3.myworkdayjobs.com"
    )
    assert host_key(Board("workday", "wd5.myworkdaysite.com/nvidia/X")) == (
        "workday:wd5.myworkdaysite.com"
    )
    assert host_key(Board("greenhouse", "stripe")) == "greenhouse"


# ----------------------------------------------------------------------------- stapply + discovery


def test_stapply_rows_backfill_other_sources_and_discover_self_polled_boards():
    from wecanfindintern.ingestion.ats.stapply import rows_to_jobs

    rows = [
        {
            "ats_type": "oracle",
            "ats_id": "77",
            "company": "Acme Bank",
            "title": "Software Engineer Intern",
            "url": "https://acme.oraclecloud.com/job/77",
            "apply_url": None,
            "country_iso": "US",
            "location": "Charlotte, NC, US",
            "posted_at": "2026-11-01T00:00:00",
            "description": "<p>Build</p>",
            "is_remote": False,
        },
        {
            "ats_type": "icims",
            "ats_id": "9",
            "company": "Acme",
            "title": "Marketing Intern",
            "url": "https://careers-acme.icims.com/jobs/9",
            "country_iso": "US",
        },
        {
            "ats_type": "tiktok",
            "ats_id": "5",
            "company": "TikTok",
            "title": "Data Science Intern",
            "url": "https://lifeattiktok.com/search/5",
            "country_iso": "GB",
            "location": "London",
        },
        {
            "ats_type": "greenhouse",
            "ats_id": "1",
            "company": "Waymo",
            "title": "Software Engineer Intern",
            "url": "https://job-boards.greenhouse.io/waymo/jobs/1",
        },
    ]
    result = rows_to_jobs(rows, Scope())
    assert [(job.source, job.source_job_id) for job in result.jobs] == [("oracle", "77")]
    job = result.jobs[0]
    assert job.description == "Build"
    assert job.date_posted == date(2026, 11, 1)
    assert parse_location(job.location_text).country_code == "US"
    assert [(b.provider, b.token) for b in result.boards] == [("greenhouse", "waymo")]
    assert result.stats["rejected_title"] == 1
    assert result.stats["rejected_location"] == 1


def test_stapply_query_filters_countries_and_staleness():
    from wecanfindintern.ingestion.ats.stapply import stapply_query

    sql, params = stapply_query(Scope(), source="x.parquet")
    assert "is_stale" in sql and "country_iso IN (?, ?)" in sql
    assert params[:3] == ["x.parquet", "CA", "US"]


def test_greenhouse_embed_candidates():
    from wecanfindintern.ingestion.ats.boards import greenhouse_embed_candidates

    assert greenhouse_embed_candidates("https://careers.withwaymo.com/jobs?gh_jid=81") == (
        "81",
        ["withwaymo", "waymo"],
    )
    job_id, candidates = greenhouse_embed_candidates(
        "https://app.careerpuck.com/job-board/lyft/job/87?gh_jid=87"
    )
    assert job_id == "87" and "lyft" in candidates
    assert greenhouse_embed_candidates("https://example.com/jobs/1") is None


def test_catalog_carries_per_source_caps():
    from wecanfindintern.ingestion.collection_catalog import expand_collection_catalog

    plans = expand_collection_catalog(
        {
            "sites": ["indeed", "linkedin"],
            "locations": [{"country_code": "US"}],
            "keyword_groups": [{"domain": "cs", "keywords": ["software engineer intern"]}],
            "defaults": {
                "max_results_per_source": 40,
                "max_results_by_source": {"indeed": 1000},
                "page_size_by_source": {"indeed": 1000},
            },
        }
    )
    assert plans[0]["max_results_per_source"] == 40
    assert plans[0]["max_results_by_source"] == {"indeed": 1000}
    assert plans[0]["page_size_by_source"] == {"indeed": 1000}


# ----------------------------------------------------------------------------- simplify


SIMPLIFY_ROWS = [
    {
        "id": "a",
        "company_name": "Acme",
        "title": "Software Engineer Intern",
        "active": True,
        "is_visible": True,
        "category": "Software",
        "locations": ["Austin, TX"],
        "url": "https://job-boards.greenhouse.io/acme/jobs/111",
        "date_posted": 1793000000,
    },
    {
        "id": "b",
        "company_name": "Bolt",
        "title": "Research Intern",
        "active": True,
        "category": "AI/ML/Data",
        "locations": ["Toronto, ON"],
        "url": "https://bolt.example.com/careers/222",
    },
    {
        "id": "c",
        "company_name": "Gone",
        "title": "Software Engineer Intern",
        "active": False,
        "locations": ["NYC"],
        "url": "https://jobs.lever.co/gone/x",
    },
    {
        "id": "d",
        "company_name": "Euro",
        "title": "Data Science Intern",
        "active": True,
        "category": "AI/ML/Data",
        "locations": ["London, UK"],
        "url": "https://euro.example.com/1",
    },
    {
        "id": "e",
        "company_name": "Shop",
        "title": "Store Associate Intern",
        "active": True,
        "category": "Other",
        "locations": ["Austin, TX"],
        "url": "https://shop.example.com/1",
    },
]


def test_simplify_parse_scope_and_discovery():
    from wecanfindintern.ingestion.ats.simplify import parse_listings

    listings, boards, stats = parse_listings({"internships": SIMPLIFY_ROWS}, Scope())
    assert [item.listing_id for item in listings] == ["a", "b"]
    assert listings[1].countries == {"CA"}
    # Inactive postings still reveal a board worth polling.
    assert [(b.provider, b.token) for b in boards] == [("greenhouse", "acme"), ("lever", "gone")]
    assert stats["out_of_scope_location"] == 1


def test_simplify_coverage_and_gap_fill():
    from wecanfindintern.domain.normalized_job import NormalizedJob
    from wecanfindintern.ingestion.ats.simplify import (
        coverage_report,
        gap_fill_jobs,
        parse_listings,
    )

    listings, _, _ = parse_listings({"internships": SIMPLIFY_ROWS}, Scope())
    collected = [
        NormalizedJob(
            source_fingerprint="x",
            source="greenhouse",
            source_job_id="111",
            source_url="https://job-boards.greenhouse.io/acme/jobs/111",
            title="Software Engineer Intern",
            company_name="Acme",
            raw={},
        )
    ]
    report = coverage_report(listings, collected)
    assert (report["reference"], report["covered"], report["recall"]) == (2, 1, 0.5)
    assert report["by_platform"]["greenhouse"]["recall"] == 1.0
    assert report["missed_sample"][0]["company"] == "Bolt"
    fill = gap_fill_jobs(listings, collected)
    assert [(job.source, job.source_job_id) for job in fill] == [("simplify", "b")]
    assert parse_location(fill[0].location_text).country_code == "CA"


def test_simplify_matches_by_company_and_title_when_ids_differ():
    from wecanfindintern.domain.normalized_job import NormalizedJob
    from wecanfindintern.ingestion.ats.simplify import coverage_report, parse_listings

    listings, _, _ = parse_listings({"internships": SIMPLIFY_ROWS[1:2]}, Scope())
    collected = [
        NormalizedJob(
            source_fingerprint="y",
            source="indeed",
            source_url="https://indeed/1",
            title="Research Intern - Summer 2027",
            company_name="Bolt Inc.",
            raw={},
        )
    ]
    assert coverage_report(listings, collected)["covered"] == 1


def test_resolve_greenhouse_embeds_confirms_token():
    from wecanfindintern.ingestion.ats.simplify import resolve_greenhouse_embeds

    seen = []

    def status(url):
        seen.append(url)
        return 200 if "/boards/waymo/" in url else 404

    boards = resolve_greenhouse_embeds(
        [
            ("https://careers.withwaymo.com/jobs?gh_jid=81", "Waymo"),
            ("https://careers.withwaymo.com/jobs?gh_jid=82", "Waymo"),
        ],
        status,
    )
    assert [(b.token, b.company) for b in boards] == [("waymo", "Waymo")]
    assert len(seen) == 2  # second URL on the same host is not re-resolved


def test_build_source_monitor_groups_collectors():
    from scripts.collection.run_collection_campaign import build_source_monitor
    from wecanfindintern.domain.normalized_job import NormalizedJob

    jobs = [
        NormalizedJob(source_fingerprint="1", source="workday", source_url="u1", title="t", raw={}),
        NormalizedJob(
            source_fingerprint="2",
            source="oracle",
            source_url="u2",
            title="t",
            raw={"via": "stapply"},
        ),
        NormalizedJob(source_fingerprint="3", source="indeed", source_url="u3", title="t", raw={}),
    ]
    monitor = build_source_monitor(jobs, [])
    assert monitor == {
        "sources": {"ats_workday": {"jobs": 1}, "indeed": {"jobs": 1}, "stapply": {"jobs": 1}}
    }


def test_final_job_counts_use_the_same_collector_names():
    from scripts.collection.run_collection_campaign import source_group
    from wecanfindintern.domain.normalized_job import NormalizedJob

    job = NormalizedJob(
        source_fingerprint="1", source="oracle", source_url="u", title="t", raw={"via": "stapply"}
    )
    assert source_group(job) == "stapply"
    gap = NormalizedJob(
        source_fingerprint="2",
        source="simplify",
        source_url="u",
        title="t",
        raw={"via": "simplify"},
    )
    assert source_group(gap) == "simplify"
