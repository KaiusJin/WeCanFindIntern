#!/usr/bin/env python3
"""P0 collector probe: measure LinkedIn/Indeed filters, result sizes and rate limits.

Standalone on purpose (requests + bs4 only): it must not depend on the collector
code being refactored. Writes results/events.jsonl, results/summary.json and
results/summary.md. Never prints credentials; this repo's Actions logs are public.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path(os.getenv("PROBE_OUT", "results"))
SMOKE = os.getenv("PROBE_SMOKE") == "1"
LI_BUDGET = float(os.getenv("PROBE_LINKEDIN_MINUTES", "85")) * 60
INDEED_BUDGET = float(os.getenv("PROBE_INDEED_MINUTES", "40")) * 60

GEO = {"US": "103644278", "CA": "101174742"}
COUNTRY_NAME = {"US": "United States", "CA": "Canada"}
FAMILIES = {
    "swe": "software engineer",
    "swe_dev": "software developer",
    "fde": "forward deployed engineer",
    "ai_ml": "machine learning",
    "ai": "artificial intelligence",
    "data_eng": "data engineer",
    "data_analysis": "data analyst",
    "data_science": "data scientist",
    "infra": "devops",
    "platform": "platform engineer",
    "systems": "systems engineer",
    "security": "security engineer",
    "qa": "software test",
    "quant_dev": "quantitative developer",
}
LI_TRACKS = {"internship": {"f_JT": "I"}, "full_time_entry": {"f_JT": "F", "f_E": "2"}}

INTERN_RE = re.compile(r"\b(intern|interns|internship|co-?op|coop|student|placement|apprentice\w*)\b", re.I)
SENIOR_RE = re.compile(
    r"\b(senior|sr|staff|principal|lead|manager|director|head|architect|vp)\b|\b(ii|iii|iv)\b", re.I
)
ENTRY_RE = re.compile(
    r"new\s*grad|graduate|entry|junior|\bjr\b|early[- ]career|university|campus|\b(i|1)\b", re.I
)
CA_RE = re.compile(
    r"canada|ontario|british columbia|quebec|alberta|manitoba|saskatchewan|nova scotia|"
    r"new brunswick|newfoundland|prince edward|\b(ON|BC|QC|AB|MB|SK|NS|NB|NL|PE)\b",
)
US_RE = re.compile(r"united states|,\s*[A-Z]{2}\b|\bUS\b|\bUSA\b")

LI_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
# Public mobile-app key shipped in upstream JobSpy (not a secret of this project).
INDEED_HEADERS = {
    "Host": "apis.indeed.com",
    "content-type": "application/json",
    "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
    "accept": "application/json",
    "indeed-locale": "en-US",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Mobile/15E148 Indeed App 193.1"
    ),
    "indeed-app-info": "appv=193.1; appid=com.indeed.jobsearch; osv=16.6.1; os=ios; dtype=phone",
}
INDEED_FIELDS = """
  pageInfo { nextCursor }
  results { job { key title datePublished dateOnIndeed
    location { countryCode admin1Code city }
    attributes { key label } } }
"""

_events_lock = threading.Lock()
T0 = time.monotonic()


def now():
    return round(time.monotonic() - T0, 1)


def event(**data):
    data = {"t": now(), **data}
    with _events_lock:
        with (OUT / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, default=str) + "\n")
    return data


def log(msg):
    print(f"[{now():7.1f}s] {msg}", flush=True)


def ratios(titles, locations=()):
    n = max(1, len(titles))
    out = {
        "n": len(titles),
        "intern_ratio": round(sum(bool(INTERN_RE.search(t)) for t in titles) / n, 3),
        "senior_ratio": round(sum(bool(SENIOR_RE.search(t)) for t in titles) / n, 3),
        "entry_signal_ratio": round(sum(bool(ENTRY_RE.search(t)) for t in titles) / n, 3),
    }
    if locations:
        m = max(1, len(locations))
        out["ca_ratio"] = round(sum(bool(CA_RE.search(x or "")) for x in locations) / m, 3)
        out["us_ratio"] = round(
            sum(bool(US_RE.search(x or "")) and not CA_RE.search(x or "") for x in locations) / m, 3
        )
    return out


# --------------------------------------------------------------------------- LinkedIn


class LinkedInProbe:
    base = "https://www.linkedin.com"

    def __init__(self, interval):
        self.session = requests.Session()
        self.session.headers.update(LI_HEADERS)
        self.interval = interval
        self.last = 0.0
        self.requests = 0
        self.rate_limits = []  # [{t, kind, interval}]
        self.recoveries = []  # [{t_429, recovered_after_s, probes}]
        self.successes_since_429 = 0

    def _pace(self):
        wait = self.last + self.interval * random.uniform(0.75, 1.5) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()

    def _get(self, path, params, kind):
        self._pace()
        self.session.cookies.clear()
        started = time.monotonic()
        try:
            r = self.session.get(self.base + path, params=params, timeout=15, allow_redirects=True)
            status = r.status_code
            final = urlparse(r.url).path
        except requests.RequestException as exc:
            event(provider="linkedin", kind=kind, status="error", error=type(exc).__name__)
            return None, "error"
        self.requests += 1
        limited = status in (429, 999) or any(
            p in final for p in ("/authwall", "/login", "/checkpoint", "/signup")
        )
        event(
            provider="linkedin", kind=kind, status=status, limited=limited,
            elapsed=round(time.monotonic() - started, 2), interval=self.interval,
            final_path=final if final != path else None,
        )
        if limited:
            self.rate_limits.append({"t": now(), "kind": kind, "status": status,
                                     "interval": self.interval, "after_ok": self.successes_since_429})
            self.successes_since_429 = 0
            return r, "limited"
        self.successes_since_429 += 1
        return r, "ok"

    def get(self, path, params, kind, deadline):
        """Retry through cooldowns (never give up) until the probe deadline."""
        r, state = self._get(path, params, kind)
        if state != "limited":
            return r
        t429 = now()
        ladder = [60, 120, 240, 480, 600, 600, 600]
        probes = 0
        for wait in ladder:
            if time.monotonic() + wait > deadline:
                self.recoveries.append({"t_429": t429, "recovered_after_s": None, "probes": probes})
                return None
            log(f"LinkedIn limited ({kind}); cooling {wait}s")
            time.sleep(wait)
            probes += 1
            r, state = self._get(path, params, kind + "_recovery_probe")
            if state == "ok":
                self.recoveries.append({"t_429": t429, "recovered_after_s": round(now() - t429, 1),
                                        "probes": probes})
                self.interval = max(self.interval, 6) * 1.5
                log(f"LinkedIn recovered after {now() - t429:.0f}s; interval now {self.interval:.1f}s")
                return r
        self.recoveries.append({"t_429": t429, "recovered_after_s": None, "probes": probes})
        return None

    @staticmethod
    def cards(html):
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for card in soup.select("div.base-search-card"):
            urn = re.search(r"(\d+)$", card.get("data-entity-urn", ""))
            link = card.select_one("a.base-card__full-link[href]")
            jid = urn[1] if urn else None
            if not jid and link:
                m = re.search(r"-(\d+)(?:\?|$)", link["href"])
                jid = m[1] if m else None
            title = card.select_one(".base-search-card__title")
            loc = card.select_one(".job-search-card__location")
            when = card.select_one("time")
            out.append({
                "id": jid,
                "title": title.get_text(strip=True) if title else "",
                "location": loc.get_text(strip=True) if loc else "",
                "date": when.get("datetime") if when else None,
            })
        return out

    def search(self, params, start, deadline, kind="search"):
        r = self.get("/jobs-guest/jobs/api/seeMoreJobPostings/search",
                     {**params, "start": start}, kind, deadline)
        if r is None:
            return None
        if r.status_code >= 400:
            return {"status": r.status_code, "cards": []}
        return {"status": r.status_code, "cards": self.cards(r.text)}

    def count(self, params, deadline):
        r = self.get("/jobs/search", {**params, "position": 1, "pageNum": 0}, "count", deadline)
        if r is None or r.status_code >= 400:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one(".results-context-header__job-count")
        if not node:
            return None
        text = node.get_text(strip=True)
        digits = re.sub(r"[^\d]", "", text)
        return {"value": int(digits) if digits else None, "raw": text}

    def detail(self, job_id, deadline):
        r = self.get(f"/jobs/view/{job_id}", {}, "detail", deadline)
        if r is None:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        return {"status": r.status_code,
                "has_description": bool(soup.select_one(".show-more-less-html__markup"))}


def li_params(keywords, country, track=None, *, geo=True, tpr=None, extra=None):
    p = {"keywords": keywords}
    if geo:
        p["geoId"] = GEO[country]
        p["location"] = COUNTRY_NAME[country]
    else:
        p["location"] = COUNTRY_NAME[country]
        p["distance"] = 100
    if track:
        p.update(LI_TRACKS[track])
    if tpr:
        p["f_TPR"] = f"r{tpr}"
    if extra:
        p.update(extra)
    return p


def run_linkedin(summary):
    li = LinkedInProbe(interval=8.0)
    deadline = time.monotonic() + (240 if SMOKE else LI_BUDGET)
    res = summary["linkedin"] = {"filters": {}, "counts": {}, "pagination": [], "rate": {}}

    # ---- E1: do the filters do what we think? (2 pages per variant)
    variants = {
        "us_location_distance100_nofilter": li_params("software engineer", "US", geo=False),
        "us_geoid_nofilter": li_params("software engineer", "US"),
        "us_geoid_fJT_I": li_params("software engineer", "US", "internship"),
        "us_geoid_fE_1": li_params("software engineer", "US", extra={"f_E": "1"}),
        "us_geoid_fJT_F_fE_2": li_params("software engineer", "US", "full_time_entry"),
        "us_geoid_fJT_I_tpr24h": li_params("software engineer", "US", "internship", tpr=86400),
        "us_or_syntax_fJT_I": li_params('"data engineer" OR "devops"', "US", "internship"),
        "ca_geoid_fJT_I": li_params("software engineer", "CA", "internship"),
        "ca_geoid_fJT_F_fE_2": li_params("software engineer", "CA", "full_time_entry"),
    }
    if SMOKE:
        variants = dict(list(variants.items())[:2])
    for name, params in variants.items():
        cards = []
        for start in (0, 10):
            page = li.search(params, start, deadline, kind="filter")
            if page is None:
                break
            cards += page["cards"]
        titles = [c["title"] for c in cards]
        stats = ratios(titles, [c["location"] for c in cards])
        if name == "us_or_syntax_fJT_I":
            stats["or_hits"] = {
                "data engineer": sum("data engineer" in t.lower() for t in titles),
                "devops": sum("devops" in t.lower() for t in titles),
            }
        stats["sample_titles"] = titles[:6]
        stats["sample_locations"] = [c["location"] for c in cards[:4]]
        stats["count_header"] = None if SMOKE else li.count(params, deadline)
        res["filters"][name] = stats
        log(f"LinkedIn filter {name}: {json.dumps({k: v for k, v in stats.items() if 'sample' not in k})}")
        if time.monotonic() > deadline:
            break

    # ---- E4: result sizes per family x track x country (full + last 24h)
    if not SMOKE:
        for country in ("US", "CA"):
            for track in LI_TRACKS:
                for fam, kw in FAMILIES.items():
                    if time.monotonic() > deadline - 1800:
                        break
                    full = li.count(li_params(kw, country, track), deadline)
                    day = li.count(li_params(kw, country, track, tpr=86400), deadline)
                    res["counts"][f"{country}/{track}/{fam}"] = {"full": full, "last_24h": day}
                    log(f"LinkedIn count {country}/{track}/{fam}: full={full} 24h={day}")

    # ---- E2 + depth: page real queries to exhaustion while stepping the interval
    stages = [(8.0, 600), (6.0, 900), (4.0, 900), (3.0, 900)] if not SMOKE else [(8.0, 60)]
    queue = [
        ("US", "internship", "swe"), ("US", "internship", "data_science"),
        ("CA", "internship", "swe"), ("US", "full_time_entry", "swe"),
        ("US", "internship", "ai_ml"), ("CA", "full_time_entry", "data_eng"),
        ("US", "full_time_entry", "infra"), ("US", "internship", "qa"),
    ]
    stage_idx, stage_end = 0, time.monotonic() + stages[0][1]
    li.interval = stages[0][0]
    stage_log = []
    for country, track, fam in queue:
        params = li_params(FAMILIES[fam], country, track)
        seen, dup, start, pages, end_reason = set(), 0, 0, 0, None
        while time.monotonic() < deadline:
            if time.monotonic() > stage_end and stage_idx + 1 < len(stages) and not li.rate_limits:
                stage_log.append({"interval": li.interval, "requests_so_far": li.requests})
                stage_idx += 1
                li.interval = stages[stage_idx][0]
                stage_end = time.monotonic() + stages[stage_idx][1]
                log(f"LinkedIn interval stage -> {li.interval}s")
            page = li.search(params, start, deadline, kind="paginate")
            if page is None:
                end_reason = "deadline_or_unrecovered"
                break
            if page["status"] >= 400:
                end_reason = f"http_{page['status']}"
                break
            pages += 1
            ids = [c["id"] for c in page["cards"]]
            if not ids:
                end_reason = "empty_page"
                break
            dup += sum(i in seen for i in ids)
            seen.update(ids)
            # One detail request per two pages, mirroring a JD queue sharing the IP budget.
            if pages % 2 == 0 and ids[0]:
                li.detail(ids[0], deadline)
            start += len(ids)
            if start >= 1000:
                end_reason = "offset_cap_1000"
                break
        else:
            end_reason = end_reason or "deadline"
        entry = {"query": f"{country}/{track}/{fam}", "pages": pages, "unique": len(seen),
                 "duplicates": dup, "last_start": start, "end": end_reason}
        res["pagination"].append(entry)
        log(f"LinkedIn paginate {entry}")
        if time.monotonic() >= deadline:
            break
    res["rate"] = {
        "requests": li.requests, "rate_limits": li.rate_limits, "recoveries": li.recoveries,
        "final_interval": li.interval, "stages_completed": stage_log,
        "limited_by_kind": dict(Counter(x["kind"] for x in li.rate_limits)),
        "minutes": round(now() / 60, 1),
    }


# --------------------------------------------------------------------------- Indeed


class IndeedProbe:
    url = "https://apis.indeed.com/graphql"

    def __init__(self, interval=2.0):
        self.session = requests.Session()
        self.interval = interval
        self.last = 0.0
        self.requests = 0
        self.rate_limits = []

    def query(self, body, country, kind):
        wait = self.last + self.interval * random.uniform(0.75, 1.5) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        headers = {**INDEED_HEADERS, "indeed-co": country}
        for attempt in range(6):
            try:
                r = self.session.post(self.url, headers=headers, json={"query": body}, timeout=20)
            except requests.RequestException as exc:
                event(provider="indeed", kind=kind, status="error", error=type(exc).__name__)
                time.sleep(10)
                continue
            self.requests += 1
            event(provider="indeed", kind=kind, status=r.status_code)
            if r.status_code == 429:
                self.rate_limits.append({"t": now(), "kind": kind})
                time.sleep(60 * (attempt + 1))
                continue
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None
        return None, None


def indeed_search(keywords, filters="", cursor=None, extra_fields="", sort="DATE"):
    what = f"what: {json.dumps(keywords)}"
    cur = f"cursor: {json.dumps(cursor)}" if cursor else ""
    return f"""query P0 {{ jobSearch({what} limit: 100 {cur} sort: {sort} {filters}) {{
        {extra_fields} {INDEED_FIELDS} }} }}"""


def attr_filter(*keys):
    return 'keyword: { field: "attributes", keys: [%s] }' % ", ".join(json.dumps(k) for k in keys)


def date_filter(hours):
    return f'date: {{ field: "dateOnIndeed", start: "{hours}h" }}'


def filters_of(*parts):
    if not parts:
        return ""
    if len(parts) == 1:
        return f"filters: {{ {parts[0]} }}"
    inner = ", ".join("{ " + p + " }" for p in parts)
    return f"filters: {{ composite: {{ filters: [{inner}] }} }}"


def indeed_results(data):
    try:
        js = data["data"]["jobSearch"]
        return [r["job"] for r in js["results"]], js["pageInfo"]["nextCursor"]
    except (TypeError, KeyError):
        return None, None


def run_indeed(summary):
    ind = IndeedProbe()
    deadline = time.monotonic() + (120 if SMOKE else INDEED_BUDGET)
    res = summary["indeed"] = {"filters": {}, "attribute_labels": {}, "pagination": [], "schema": {}}

    def one(name, keywords, country, parts, pages=1, sort="DATE", raw_filters=None):
        jobs, cursor, errors = [], None, None
        for _ in range(pages):
            flt = raw_filters if raw_filters is not None else filters_of(*parts)
            status, data = ind.query(indeed_search(keywords, flt, cursor, sort=sort), country, name)
            if data and data.get("errors"):
                errors = [e.get("message", "")[:200] for e in data["errors"]][:3]
            got, cursor = indeed_results(data)
            if not got:
                break
            jobs += got
            if not cursor:
                break
        titles = [j.get("title") or "" for j in jobs]
        attr_keys = Counter(a["key"] for j in jobs for a in (j.get("attributes") or []))
        stats = ratios(titles)
        stats.update({
            "countries": dict(Counter((j.get("location") or {}).get("countryCode") for j in jobs)),
            "errors": errors,
            "share_with_VDTG7": round(attr_keys.get("VDTG7", 0) / max(1, len(jobs)), 3),
            "share_with_CF3CP": round(attr_keys.get("CF3CP", 0) / max(1, len(jobs)), 3),
            "sample_titles": titles[:6],
            "has_next_cursor": bool(cursor),
            "date_sorted_desc": all(
                (a.get("dateOnIndeed") or 0) >= (b.get("dateOnIndeed") or 0) for a, b in zip(jobs, jobs[1:])
            ),
        })
        res["filters"][name] = stats
        log(f"Indeed {name}: {json.dumps({k: v for k, v in stats.items() if k != 'sample_titles'})}")
        return jobs

    one("us_nofilter", "software engineer", "US", ())
    one("us_internship_VDTG7", "software engineer", "US", (attr_filter("VDTG7"),))
    one("us_date24h_only", "software engineer", "US", (date_filter(24),))
    one("us_composite_date24h_VDTG7", "software engineer", "US", (date_filter(24), attr_filter("VDTG7")))
    ft = one("us_fulltime_CF3CP", "software engineer", "US", (attr_filter("CF3CP"),), pages=1 if SMOKE else 3)
    one("ca_internship_VDTG7", "software engineer", "CA", (attr_filter("VDTG7"),))
    one("us_internship_VDTG7_relevance", "software engineer", "US", (attr_filter("VDTG7"),), sort="RELEVANCE")
    one("us_kw_intern_nofilter", "software engineer intern", "US", ())
    one("us_kw_intern_VDTG7", "software engineer intern", "US", (attr_filter("VDTG7"),))
    # Composite cannot hold a date filter; try date + keyword side by side at top level.
    one("us_toplevel_date24h_and_VDTG7", "software engineer", "US", (),
        raw_filters="filters: { %s, %s }" % (date_filter(24), attr_filter("VDTG7")))

    # Attribute label catalogue (look for an entry-level/new-grad key).
    labels = Counter()
    for j in ft:
        for a in j.get("attributes") or []:
            labels[(a["key"], a["label"])] += 1
    res["attribute_labels"] = {f"{k} | {lbl}": n for (k, lbl), n in labels.most_common(60)}
    entry_keys = [k for (k, lbl) in labels if re.search(r"entry|new grad|junior|graduate|no experience", lbl, re.I)]
    res["entry_level_keys_found"] = {k: lbl for (k, lbl) in labels if k in entry_keys}
    for key in entry_keys[:3]:
        one(f"us_fulltime_CF3CP_and_{key}", "software engineer", "US", (attr_filter("CF3CP"), attr_filter(key)))
        one(f"us_attr_{key}_only", "software engineer", "US", (attr_filter(key),))

    # Does jobSearch expose a total count? (GraphQL errors reveal unknown fields.)
    for field in ("totalCount", "totalResults", "estimatedTotalResultCount"):
        status, data = ind.query(indeed_search("software engineer", "", None, field), "US", f"schema_{field}")
        msg = (data or {}).get("errors")
        value = None if msg else ((data or {}).get("data") or {}).get("jobSearch", {}).get(field)
        res["schema"][field] = {"ok": not msg, "value": value,
                                "error": msg[0].get("message", "")[:200] if msg else None}

    # Pagination depth.
    plans = [("US", "software engineer", (attr_filter("VDTG7"),)),
             ("CA", "software engineer", (attr_filter("VDTG7"),)),
             ("US", "data scientist", (attr_filter("VDTG7"),)),
             ("US", "software engineer intern", ()),
             ("US", "software engineer", (attr_filter("CF3CP"), attr_filter("Y4JG9"))),
             ("US", "software engineer", (attr_filter("CF3CP"),))]
    for country, kw, parts in plans[:1] if SMOKE else plans:
        cursor, pages, seen, dup, end = None, 0, set(), 0, None
        while time.monotonic() < deadline:
            status, data = ind.query(indeed_search(kw, filters_of(*parts), cursor), country, "paginate")
            got, nxt = indeed_results(data)
            if got is None:
                end = f"bad_response_{status}"
                break
            pages += 1
            keys = [j["key"] for j in got]
            dup += sum(k in seen for k in keys)
            seen.update(keys)
            if not got or not nxt:
                end = "no_next_cursor" if got else "empty_page"
                break
            cursor = nxt
            if pages >= (2 if SMOKE else 60):
                end = "probe_page_limit"
                break
        entry = {"query": f"{country}/{kw}/{parts}", "pages": pages, "unique": len(seen),
                 "duplicates": dup, "end": end or "deadline"}
        res["pagination"].append(entry)
        log(f"Indeed paginate {entry}")
    res["rate"] = {"requests": ind.requests, "rate_limits": ind.rate_limits}


# --------------------------------------------------------------------------- report


def write_markdown(summary):
    li, ind = summary.get("linkedin", {}), summary.get("indeed", {})
    lines = [f"# Collector P0 probe ({summary['started_at']})", "",
             f"Runner: `{summary['runner']}` · smoke={SMOKE}", "", "## LinkedIn filters", "",
             "| variant | n | intern | senior | entry-signal | US | CA | count header |", "|---|---|---|---|---|---|---|---|"]
    for name, s in li.get("filters", {}).items():
        lines.append(f"| {name} | {s['n']} | {s['intern_ratio']} | {s['senior_ratio']} | "
                     f"{s['entry_signal_ratio']} | {s.get('us_ratio')} | {s.get('ca_ratio')} | {(s.get('count_header') or {}).get('raw')} |")
    if li.get("counts"):
        lines += ["", "## LinkedIn result counts (count header)", "", "| query | full | last 24h |", "|---|---|---|"]
        fmt = lambda c: (c or {}).get("raw") if isinstance(c, dict) else c
        lines += [f"| {k} | {fmt(v['full'])} | {fmt(v['last_24h'])} |" for k, v in li["counts"].items()]
    lines += ["", "## LinkedIn pagination", "", "| query | pages | unique | dup | end |", "|---|---|---|---|---|"]
    lines += [f"| {p['query']} | {p['pages']} | {p['unique']} | {p['duplicates']} | {p['end']} |"
              for p in li.get("pagination", [])]
    lines += ["", "## LinkedIn rate", "", "```json", json.dumps(li.get("rate", {}), indent=1), "```",
              "", "## Indeed filters", "", "| variant | n | intern | senior | VDTG7 | CF3CP | countries | errors |",
              "|---|---|---|---|---|---|---|---|"]
    for name, s in ind.get("filters", {}).items():
        lines.append(f"| {name} | {s['n']} | {s['intern_ratio']} | {s['senior_ratio']} | {s['share_with_VDTG7']} | "
                     f"{s['share_with_CF3CP']} | {s['countries']} | {s['errors']} |")
    lines += ["", f"Entry-level attribute keys found: {ind.get('entry_level_keys_found')}", "",
              "## Indeed pagination", "", "| query | pages | unique | dup | end |", "|---|---|---|---|---|"]
    lines += [f"| {p['query']} | {p['pages']} | {p['unique']} | {p['duplicates']} | {p['end']} |"
              for p in ind.get("pagination", [])]
    lines += ["", "## Indeed schema / rate", "", "```json",
              json.dumps({"schema": ind.get("schema"), "rate": ind.get("rate")}, indent=1), "```"]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "events.jsonl").write_text("", encoding="utf-8")
    summary = {"started_at": datetime.now(UTC).isoformat(timespec="seconds"),
               "runner": os.getenv("RUNNER_NAME", "local"), "errors": {}}

    def guarded(fn, key):
        try:
            fn(summary)
        except Exception as exc:  # keep the other provider's results
            summary["errors"][key] = f"{type(exc).__name__}: {exc}"
            log(f"{key} probe crashed: {summary['errors'][key]}")

    threads = [threading.Thread(target=guarded, args=(run_linkedin, "linkedin")),
               threading.Thread(target=guarded, args=(run_indeed, "indeed"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    summary["finished_after_minutes"] = round(now() / 60, 1)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    write_markdown(summary)
    log("done")


if __name__ == "__main__":
    main()
