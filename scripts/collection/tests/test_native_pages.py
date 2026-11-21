"""Exercise the campaign with actual provider page parsing and an interrupted page journal."""

import asyncio
import json
from contextlib import closing

import jobspy.indeed as indeed
import jobspy.linkedin as linkedin
from requests import Response, Session

from scripts.collection.checkpoint import QueryCheckpoint
from scripts.collection.run_collection_campaign import collect_all


def response(body, *, status=200, url="https://www.linkedin.com/jobs-guest/search"):
    result = Response()
    result.status_code = status
    result.url = url
    result._content = body.encode()
    result._content_consumed = True
    return result


def definition(source):
    return [
        {
            "name": "intern-ca",
            "sites": [source],
            "query": {"search_term": "intern", "country_indeed": "Canada", "location": "Canada"},
        }
    ]


def card(number, location="Toronto, ON, Canada"):
    return f"""
      <div class="base-search-card">
      <a class="base-card__full-link" href="/jobs/view/intern-{number}"></a>
      <span class="sr-only">Software Intern</span><div class="base-search-card__metadata">
      <span class="job-search-card__location">{location}</span></div></div>
    """


def linkedin_pages(monkeypatch, pages):
    calls = []

    def get(_session, _url, *, params, **kwargs):
        start = params["start"]
        calls.append(start)
        return pages[start]

    monkeypatch.setattr(linkedin.request_gate, "get", get)
    return calls


def test_raw_linkedin_offsets_continue_past_duplicates_filters_and_forty(monkeypatch):
    first = "".join(card(i) for i in range(25))
    pages = {
        0: response(first),
        25: response(first),
        50: response(card(800, "Seattle, WA, USA")),
        51: response("".join(card(i, "Canada") for i in range(25, 50))),
        76: response(""),
    }
    calls = linkedin_pages(monkeypatch, pages)
    jobs, errors, stats = asyncio.run(collect_all(definition("linkedin")))
    assert calls == [0, 25, 50, 51, 76]
    assert {job.source_job_id for job in jobs} == {f"li-{i}" for i in range(50)}
    assert not errors and stats["succeeded"] == 1


def indeed_page(keys, following):
    results = [
        {
            "job": {
                "key": key,
                "title": "Software Intern",
                "description": {"html": "<p>Build tools</p>"},
                "attributes": [],
                "datePublished": 1795000000000,
                "employer": None,
                "recruit": None,
                "compensation": {"baseSalary": None, "estimated": None},
                "location": {
                    "city": "Toronto",
                    "admin1Code": "ON",
                    "countryCode": "CA",
                    "formatted": {"long": "Toronto, ON"},
                },
            }
        }
        for key in keys
    ]
    return response(
        json.dumps(
            {"data": {"jobSearch": {"results": results, "pageInfo": {"nextCursor": following}}}}
        )
    )


def test_indeed_resumes_exact_cursor_after_error_and_filtered_page(monkeypatch, tmp_path):
    replies = [
        indeed_page(["a"], "opaque-1"),
        indeed_page([], "opaque-2"),
        response("", status=503),
        indeed_page(["b"], None),
    ]
    requests = []

    def post(url, *, json, **kwargs):
        requests.append(json["query"])
        return replies.pop(0)

    def session(**kwargs):
        result = Session()
        result.post = post
        return result

    monkeypatch.setattr(indeed, "create_session", session)
    config = tmp_path / "plans.json"
    config.write_text("[]")

    def attempt():
        journal = QueryCheckpoint.local(tmp_path, config, "local-test")
        with closing(journal.db):
            journal.restore(1)
            return asyncio.run(collect_all(definition("indeed"), checkpoint=journal, max_retries=0))

    jobs, errors, stats = attempt()
    assert [j.source_job_id for j in jobs] == ["in-a"]
    assert stats["failed"] == 1 and errors
    jobs, errors, stats = attempt()
    assert {j.source_job_id for j in jobs} == {"in-a", "in-b"}
    assert stats["succeeded"] == 1 and not errors
    assert len(requests) == 4 and "cursor:" not in requests[0]
    assert 'cursor: "opaque-1"' in requests[1]
    assert all('cursor: "opaque-2"' in query for query in requests[2:])
