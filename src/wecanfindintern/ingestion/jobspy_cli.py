"""Shared CLI argument handling for JobSpy scripts."""

from __future__ import annotations

import argparse

from wecanfindintern.ingestion.jobspy_adapter import SUPPORTED_SITES, JobSpyQuery


def add_query_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--site",
        action="append",
        dest="sites",
        choices=sorted(SUPPORTED_SITES),
        help="Repeatable; defaults to indeed",
    )
    parser.add_argument("--search-term", required=True)
    parser.add_argument("--google-search-term")
    parser.add_argument("--location")
    parser.add_argument("--country-indeed", default="Canada")
    parser.add_argument("--distance", type=int, default=50)
    parser.add_argument("--results-wanted", type=int, default=20)
    parser.add_argument("--hours-old", type=int)
    parser.add_argument(
        "--job-type",
        choices=["fulltime", "parttime", "internship", "contract"],
    )
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--easy-apply", action="store_true", default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--linkedin-fetch-description", action="store_true")
    parser.add_argument("--enforce-annual-salary", action="store_true")
    parser.add_argument(
        "--description-format",
        choices=["markdown", "html", "plain"],
        default="markdown",
    )
    parser.add_argument("--proxy", action="append", dest="proxies")
    parser.add_argument("--verbose", type=int, choices=[0, 1, 2], default=1)


def query_from_args(args: argparse.Namespace) -> JobSpyQuery:
    return JobSpyQuery(
        sites=args.sites or ["indeed"],
        search_term=args.search_term,
        google_search_term=args.google_search_term,
        location=args.location,
        country_indeed=args.country_indeed,
        distance=args.distance,
        results_wanted=args.results_wanted,
        hours_old=args.hours_old,
        job_type=args.job_type,
        is_remote=args.remote,
        easy_apply=args.easy_apply,
        offset=args.offset,
        linkedin_fetch_description=args.linkedin_fetch_description,
        enforce_annual_salary=args.enforce_annual_salary,
        description_format=args.description_format,
        proxies=args.proxies,
        verbose=args.verbose,
    )
