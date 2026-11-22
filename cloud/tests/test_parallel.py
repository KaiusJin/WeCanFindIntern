"""Parallel campaign: shard matrices, artifact round-trips and the publish merge."""

from __future__ import annotations

import json
from pathlib import Path

from cloud import parallel
from cloud.sources.job_boards.boards import Board
from cloud.sources.job_boards.simplify import SimplifyListing
from wecanfindintern.domain.normalized_job import NormalizedJob


def job(fingerprint: str, source: str = "indeed", description: str | None = None) -> NormalizedJob:
    return NormalizedJob(
        source_fingerprint=fingerprint,
        source=source,
        source_url=f"https://example.com/{fingerprint}",
        title="Software Engineer Intern",
        description=description,
        raw={},
    )


def definition(term: str, country: str, sites=("indeed", "linkedin")) -> dict:
    return {
        "name": f"{country}-{term}",
        "enabled": True,
        "sites": list(sites),
        "query": {"search_term": term, "location": {"country_code": country}},
    }


def test_linkedin_matrix_has_one_shard_per_search_term():
    definitions = [
        definition("software engineer intern", "US"),
        definition("software engineer intern", "CA"),
        definition("MLOps intern", "US"),
        definition("data analyst intern", "US", sites=("indeed",)),
    ]
    assert parallel.linkedin_shards(definitions) == [
        {"shard": "mlops-intern", "term": "MLOps intern"},
        {"shard": "software-engineer-intern", "term": "software engineer intern"},
    ]
    selected = parallel._definitions_for(definitions, "linkedin", "software-engineer-intern")
    assert [d["name"] for d in selected] == [
        "US-software engineer intern",
        "CA-software engineer intern",
    ]
    assert all(d["sites"] == ["linkedin"] for d in selected)
    assert len(parallel._definitions_for(definitions, "indeed", None)) == 4


def test_workday_shards_split_big_pods_and_pool_small_ones(monkeypatch):
    monkeypatch.setattr(parallel, "WORKDAY_BOARDS_PER_SHARD", 10)
    monkeypatch.setattr(parallel, "WORKDAY_SHARD_MIN_BOARDS", 5)
    boards = [Board("workday", f"t{i}.wd1.myworkdayjobs.com/t{i}/Site") for i in range(25)]
    boards += [Board("workday", f"s{i}.wd3.myworkdayjobs.com/s{i}/Site") for i in range(6)]
    boards += [Board("workday", "x.wd12.myworkdayjobs.com/x/Site")]
    boards += [Board("greenhouse", "stripe")]
    shards = parallel.workday_shards(boards)
    assert [s["shard"] for s in shards] == ["wd1-1", "wd1-2", "wd1-3", "wd3", "misc"]
    assert shards[-1]["pods"] == ["workday:wd12.myworkdayjobs.com"]
    wd1 = [b for b in boards if ".wd1." in b.token]
    slices = [[b for b in wd1 if parallel.in_part(b, s["part"])] for s in shards[:3]]
    assert sum(len(part) for part in slices) == len(wd1)  # every board in exactly one slice
    assert not set(map(id, slices[0])) & set(map(id, slices[1]))


def test_jobs_and_listings_round_trip(tmp_path: Path):
    path = tmp_path / "jobs.jsonl.gz"
    parallel.write_jobs(path, [job("a"), job("b", "linkedin")])
    assert [j.source_fingerprint for j in parallel.read_jobs(path)] == ["a", "b"]
    assert parallel.read_jobs(tmp_path / "missing.jsonl.gz") == []
    listing = SimplifyListing(
        "internships",
        "1",
        "Acme",
        "SWE Intern",
        "https://x",
        ["NYC"],
        {"US"},
        "internship",
        1793000000,
        "Software",
    )
    restored = parallel._listing_from_dict(
        json.loads(json.dumps(parallel._listing_to_dict(listing)))
    )
    assert restored == listing


def _shard(root: Path, name: str, kind: str, jobs: list[NormalizedJob], stats=None, failures=()):
    out = root / f"out-{name}"
    parallel.write_jobs(out / "jobs.jsonl.gz", jobs)
    parallel.write_json(
        out / "result.json",
        {
            "name": name,
            "kind": kind,
            "jobs": len(jobs),
            "seconds": 5,
            "stats": stats or {},
            "failures": list(failures),
        },
    )


def test_gather_artifacts_merges_shards_and_flags_missing_ones(tmp_path: Path):
    parallel.write_json(
        tmp_path / "plan" / "plan.json",
        {
            "sweep_number": 3,
            "sweep_mode": "full",
            "hours_old": None,
            "plan_count": 4,
            "linkedin": [{"shard": "swe", "term": "swe"}, {"shard": "ml", "term": "ml"}],
        },
    )
    parallel.write_json(
        tmp_path / "discover" / "discover.json",
        {
            "stats": {"stapply_jobs_kept": 1},
            "seconds": 60,
            "workday": [
                {"shard": "wd1", "pods": ["workday:wd1.myworkdayjobs.com"], "part": [0, 1]}
            ],
        },
    )
    parallel.write_jobs(tmp_path / "discover" / "stapply.jsonl.gz", [job("s", "oracle")])
    parallel.write_json(tmp_path / "discover" / "simplify.json", [])
    _shard(
        tmp_path,
        "indeed",
        "indeed",
        [job("dup"), job("i")],
        stats={"succeeded": 2, "indeed_succeeded": 2},
    )
    _shard(
        tmp_path,
        "linkedin-swe",
        "linkedin",
        [job("dup", description="longer JD text")],
        stats={"succeeded": 1, "failed": 1},
        failures=["us-swe:linkedin:429"],
    )
    _shard(tmp_path, "boards", "boards", [job("g", "greenhouse")], stats={"ats_boards_ok": 9})

    collected = parallel.gather_artifacts(tmp_path)
    by_id = {j.source_fingerprint: j for j in collected.jobs}
    assert set(by_id) == {"dup", "i", "s", "g"}
    assert by_id["dup"].description == "longer JD text"  # best record wins
    assert collected.query_stats["succeeded"] == 3 and collected.query_stats["failed"] == 1
    assert collected.query_stats["stapply_jobs_kept"] == 1
    missing = sorted(name for name, s in collected.shards.items() if s.get("missing"))
    assert missing == ["linkedin-ml", "workday-wd1"]
    assert any("linkedin-ml" in f for f in collected.failures)
    assert "us-swe:linkedin:429" in collected.failures


def test_gather_artifacts_without_discover_still_publishes(tmp_path: Path):
    parallel.write_json(
        tmp_path / "plan" / "plan.json",
        {
            "sweep_number": 1,
            "sweep_mode": "full",
            "hours_old": None,
            "linkedin": [],
        },
    )
    _shard(tmp_path, "indeed", "indeed", [job("i")])
    collected = parallel.gather_artifacts(tmp_path)
    assert [j.source_fingerprint for j in collected.jobs] == ["i"]
    assert any(f.startswith("discover:") for f in collected.failures)


def test_set_output_writes_github_output(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    parallel.set_output("workday", [{"shard": "wd1"}])
    assert target.read_text() == 'workday=[{"shard":"wd1"}]\n'


def test_only_missing_shards_are_hard_failures(tmp_path: Path):
    parallel.write_json(
        tmp_path / "plan" / "plan.json",
        {
            "sweep_number": 1,
            "sweep_mode": "full",
            "hours_old": None,
            "linkedin": [{"shard": "swe", "term": "swe"}, {"shard": "ml", "term": "ml"}],
        },
    )
    _shard(tmp_path, "indeed", "indeed", [job("i")])
    _shard(
        tmp_path,
        "linkedin-swe",
        "linkedin",
        [job("l", "linkedin")],
        failures=["us-swe:linkedin:unfinished: still rate limited at the shard deadline"],
    )
    collected = parallel.gather_artifacts(tmp_path)
    assert sorted(collected.hard_failures) == [
        "discover: artifact missing (SimplifyJobs/Stapply/boards skipped)",
        "linkedin-ml: shard produced no artifact (failed, cancelled or timed out)",
    ]
    assert len(collected.failures) == 3  # the unfinished LinkedIn query stays soft


def test_signal_outcome_is_red_only_for_hard_failures(capsys):
    from types import SimpleNamespace

    import pytest

    from cloud.run import signal_outcome

    soft = SimpleNamespace(failures=("us-swe:linkedin:429",), hard_failures=())
    signal_outcome(soft)  # green: annotation only
    assert "::warning title=Collection::1 soft failures" in capsys.readouterr().out

    hard = SimpleNamespace(
        failures=("workday-wd1: shard produced no artifact", "us-swe:linkedin:429"),
        hard_failures=("workday-wd1: shard produced no artifact",),
    )
    with pytest.raises(RuntimeError, match="1 hard failures"):
        signal_outcome(hard)
    signal_outcome(SimpleNamespace(failures=(), hard_failures=()))
