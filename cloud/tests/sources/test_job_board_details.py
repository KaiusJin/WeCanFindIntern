"""Descriptions for SimplifyJobs gap-fill postings: ATS APIs and JSON-LD."""

from __future__ import annotations

import json

from cloud.sources.job_boards import details

LONG = "<p>" + "Build compilers and distributed systems with our team. " * 6 + "</p>"


class FakeHttp:
    def __init__(self, routes: dict[str, tuple[int, str]]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, accept_json: bool = False):
        self.calls.append(url)
        return self.routes.get(url, (404, ""))


def run(url: str, routes: dict[str, tuple[int, str]]):
    http = FakeHttp(routes)
    method, fetch = details.resolve(url)
    return method, fetch(http, details._AshbyBoards()), http.calls


def test_greenhouse_and_embedded_greenhouse():
    api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"
    routes = {api: (200, json.dumps({"content": "&lt;p&gt;" + "Ship. " * 20 + "&lt;/p&gt;"}))}
    method, text, _ = run("https://job-boards.greenhouse.io/acme/jobs/123", routes)
    assert method == "greenhouse" and text.startswith("Ship.")

    embed = "https://careers.withwaymo.com/jobs?gh_jid=77"
    waymo = "https://boards-api.greenhouse.io/v1/boards/waymo/jobs/77"
    method, text, calls = run(embed, {waymo: (200, json.dumps({"content": LONG}))})
    assert method == "greenhouse_embed" and "compilers" in text
    assert calls[-1] == waymo  # tried the host-derived tokens until one matched


def test_workday_lever_ashby_smartrecruiters_workable_bamboo():
    cases = [
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Toronto/SWE-Intern_R1",
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Toronto/SWE-Intern_R1",
            {"jobPostingInfo": {"jobDescription": LONG}},
            "workday",
        ),
        (
            "https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555",
            "https://api.lever.co/v0/postings/acme/11111111-2222-3333-4444-555555555555",
            {
                "descriptionPlain": "Build compilers and distributed systems. " * 3,
                "lists": [{"text": "You will", "content": "<li>ship</li>"}],
            },
            "lever",
        ),
        (
            "https://jobs.smartrecruiters.com/Acme/7444/software-intern",
            "https://api.smartrecruiters.com/v1/companies/Acme/postings/7444",
            {"jobAd": {"sections": {"jobDescription": {"text": LONG}}}},
            "smartrecruiters",
        ),
        (
            "https://apply.workable.com/acme/j/AB12CD/",
            "https://apply.workable.com/api/v2/accounts/acme/jobs/AB12CD",
            {"description": LONG, "requirements": "<p>Python and C++ experience.</p>"},
            "workable",
        ),
        (
            "https://acme.bamboohr.com/careers/42",
            "https://acme.bamboohr.com/careers/42/detail",
            {"result": {"jobOpening": {"description": LONG}}},
            "bamboohr",
        ),
    ]
    for url, api, payload, expected in cases:
        method, text, _ = run(url, {api: (200, json.dumps(payload))})
        assert method == expected, url
        assert text and "compilers" in text, url

    ashby_url = "https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555"
    board = "https://api.ashbyhq.com/posting-api/job-board/acme"
    payload = {"jobs": [{"id": "11111111-2222-3333-4444-555555555555", "descriptionHtml": LONG}]}
    method, text, _ = run(ashby_url, {board: (200, json.dumps(payload))})
    assert method == "ashby" and "compilers" in text


def test_json_ld_from_any_career_page():
    page = f"""<html><head>
    <script type="application/ld+json">{{"@context": "https://schema.org", "@graph": [
      {{"@type": "Organization", "name": "Acme"}},
      {{"@type": "JobPosting", "title": "SWE Intern", "description": {json.dumps(LONG)}}}
    ]}}</script></head></html>"""
    method, text, _ = run(
        "https://careers.acme.com/jobs/9", {"https://careers.acme.com/jobs/9": (200, page)}
    )
    assert method == "json_ld" and "compilers" in text
    assert details.description_from_html("<html>no structured data</html>") is None
    assert details.description_from_html('<script type="application/ld+json">{bad</script>') is None


def test_fetch_descriptions_counts_and_respects_budget(monkeypatch):
    monkeypatch.setattr(details, "MIN_HOST_INTERVAL_SECONDS", 0)
    ok = "https://jobs.smartrecruiters.com/Acme/1/x"
    blocked = "https://www.tesla.com/careers/search/job/1"
    routes = {
        "https://api.smartrecruiters.com/v1/companies/Acme/postings/1": (
            200,
            json.dumps({"jobAd": {"sections": {"jobDescription": {"text": LONG}}}}),
        ),
        blocked: (403, ""),
    }
    result = details.fetch_descriptions([ok, blocked, ok], http_factory=lambda: FakeHttp(routes))
    assert set(result.descriptions) == {ok}
    assert result.stats["found_smartrecruiters"] == 1 and result.stats["missing_json_ld"] == 1
    assert result.stats["requested"] == 2  # duplicates fetched once

    expired = details.fetch_descriptions(
        [ok], budget_seconds=-1, http_factory=lambda: FakeHttp(routes)
    )
    assert expired.descriptions == {} and expired.stats["skipped_deadline"] == 1
