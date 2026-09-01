"""WaterlooWorks page extraction and provider-to-domain mapping."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from wecanfindintern.domain.normalized_job import (
    CompanyDetails,
    NormalizedJob,
    build_source_fingerprint,
)
from wecanfindintern.domain.salary import ParsedSalary, extract_salary_from_description
from wecanfindintern.waterlooworks.browser_scripts import (
    WATERLOOWORKS_EXTRACTION_HELPERS,
)
from wecanfindintern.waterlooworks.config import WATERLOOWORKS_ORIGIN
from wecanfindintern.waterlooworks.taxonomy import (
    WATERLOOWORKS_BOARD_EMPLOYMENT_EVIDENCE,
)
from wecanfindintern.waterlooworks.text import clean_waterlooworks_text

# Structured numeric rate fields, e.g. "Rate Of Pay Per Hour" -> hourly.
_RATE_FIELD_INTERVALS = {
    "rate of pay per hour": ("hourly", "/hour"),
    "rate of pay per week": ("weekly", "/week"),
    "rate of pay per month": ("monthly", "/month"),
    "rate of pay per year": ("yearly", "/year"),
}

# Fields whose value often mixes benefits prose with a real pay figure.
_COMPENSATION_LABELS = (
    "compensation and benefits",
    "compensation and benefits information",
    "compensation and hours",
    "total rewards",
    "compensation",
)

_BOILERPLATE_VALUES = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "not specified",
    "tbd",
    "to be discussed",
}

# This runs inside the authenticated WaterlooWorks job-list page. Authentication
# remains entirely in Chrome; only the extracted posting records cross into the app.
EXTRACT_JOBS_SCRIPT = WATERLOOWORKS_EXTRACTION_HELPERS + r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function getPageRows() {
    return [...document.querySelectorAll("table tbody tr")]
      .map((row) => {
        const text = row.innerText || "";
        const id = text.match(/\b\d{6}\b/)?.[0];
        const link = row.querySelector("a");
        if (!id || !link) return null;
        let sourceUrl = location.href;
        try {
          sourceUrl = new URL(link.getAttribute("href") || "", location.href).href;
        } catch (_) {}
        return {
          id: Number(id),
          title: cleanText(link.innerText),
          rowText: cleanText(text),
          sourceUrl,
        };
      })
      .filter(Boolean);
  }

  async function fetchOne(row) {
    try {
      const [metadata, overviewHtml] = await Promise.all([
        callWW(getPostingData, row.id, "getPostingData"),
        callWW(getPostingOverview, row.id, "getPostingOverview"),
      ]);
      const parsed = parseOverview(overviewHtml);
      const geo = metadata.geoData || {};
      const app = metadata.applicationData || {};
      return {
        source: "waterlooworks",
        id: metadata.id || row.id,
        title: metadata.position || row.title || "",
        organization: metadata.org || "",
        division: metadata.div || "",
        sourceUrl: row.sourceUrl || location.href,
        status: {
          internal: metadata.internalStatus || "",
          approval: metadata.status || "",
          tags: metadata.tags || [],
        },
        location: {
          address: geo.address || "",
          city: geo.city || "",
          province: geo.province || "",
          postalCode: geo.postalCode || "",
          country: geo.country || "",
          latitude: geo.latitude ?? null,
          longitude: geo.longitude ?? null,
        },
        application: {
          canApply: app.canApply ?? null,
          hasApplication: app.hasApplication ?? null,
          deadline: parsed.fields["Application Deadline"] || "",
          delivery: parsed.fields["Application Delivery"] || "",
          url: parsed.fields["If by Website, go to"] || "",
          documentsRequired: parsed.fields["Application Documents Required"] || "",
        },
        overviewFields: parsed.fields,
        overviewLists: parsed.lists,
        fullJdText: htmlToText(overviewHtml),
      };
    } catch (error) {
      return {
        source: "waterlooworks",
        id: row.id,
        title: row.title,
        sourceUrl: row.sourceUrl || location.href,
        rowText: row.rowText,
        error: String(error),
      };
    }
  }

  if (typeof getPostingData !== "function" || typeof getPostingOverview !== "function") {
    throw new Error("Open a WaterlooWorks job search results page before collecting.");
  }

  const jobs = [];
  const seen = new Set();
  let page = 0;
  while (true) {
    page += 1;
    const rows = getPageRows();
    const pageIds = [...new Set(rows.map((row) => row.id))];
    for (let i = 0; i < rows.length; i += 3) {
      const results = await Promise.all(rows.slice(i, i + 3).map(fetchOne));
      for (const job of results) {
        if (!seen.has(job.id)) {
          seen.add(job.id);
          jobs.push(job);
        }
      }
    }
    const next = document.querySelector("a[aria-label='Go to next page']");
    if (!next || next.classList.contains("disabled") || next.closest(".disabled")) break;
    const oldFirstId = pageIds[0];
    next.click();
    let changed = false;
    for (let i = 0; i < 40; i += 1) {
      await sleep(500);
      const nextId = getPageRows()[0]?.id;
      if (nextId && nextId !== oldFirstId) {
        changed = true;
        break;
      }
    }
    if (!changed) break;
  }
  return { generatedAt: new Date().toISOString(), pageCount: page, count: jobs.length, jobs };
})()
"""


def normalize_waterlooworks_job(raw: dict[str, Any]) -> NormalizedJob:
    """Map one extracted WaterlooWorks posting into the existing stable boundary."""

    source_id = _text(raw.get("id"))
    if not source_id:
        raise ValueError("WaterlooWorks posting has no Job ID")
    title = _text(raw.get("title"))
    if not title:
        raise ValueError(f"WaterlooWorks posting {source_id} has no title")

    location = raw.get("location") or {}
    location_text = ", ".join(
        value
        for value in (
            _text(location.get("city")),
            _text(location.get("province")),
            _text(location.get("country")),
        )
        if value
    ) or _text(location.get("address"))
    overview = raw.get("overviewFields") or {}
    workplace = _first_field(
        overview,
        "Workplace Type",
        "Work Arrangement",
        "Work Term Location",
    )
    searchable = " ".join(
        value for value in (location_text, workplace, _text(raw.get("fullJdText"))) if value
    ).casefold()
    is_remote = True if "remote" in searchable else None
    source_url = _safe_waterlooworks_url(raw.get("sourceUrl"))
    if source_url == WATERLOOWORKS_ORIGIN and raw.get("jobBoardUrl"):
        source_url = _safe_waterlooworks_url(raw.get("jobBoardUrl"))
    application_url = _text((raw.get("application") or {}).get("url"))
    direct_url = application_url if application_url.startswith(("https://", "http://")) else None

    raw_payload = dict(raw)
    raw_payload["provider_schema"] = "waterlooworks.mvp.v1"
    raw_payload["application_deadline"] = _text(
        (raw.get("application") or {}).get("deadline")
    )
    board = _text(raw.get("jobBoard"))
    employment_types = list(WATERLOOWORKS_BOARD_EMPLOYMENT_EVIDENCE.get(board, ()))

    return NormalizedJob(
        source_fingerprint=build_source_fingerprint("waterlooworks", source_id, source_url),
        source="waterlooworks",
        source_job_id=source_id,
        source_url=source_url,
        direct_url=direct_url,
        title=title,
        company_name=_text(raw.get("organization")) or None,
        location_text=location_text or None,
        date_posted=_posting_date(overview),
        employment_types=employment_types,
        is_remote=is_remote,
        job_function=_text(raw.get("division")) or None,
        description=_description(raw.get("fullJdText")),
        company=CompanyDetails(addresses=_text(location.get("address")) or None),
        work_from_home_type=workplace or None,
        raw=raw_payload,
    )


def _text(value: Any) -> str:
    return clean_waterlooworks_text(value)


def _description(value: Any) -> str | None:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    normalized = "\n".join(line for line in lines if line)
    if normalized.casefold() in {
        "there was an error loading this job posting",
        "error loading this job posting",
    }:
        return None
    return normalized or None


def _first_field(fields: dict[str, Any], *labels: str) -> str:
    for label in labels:
        value = _text(fields.get(label))
        if value:
            return value
    return ""


def _safe_waterlooworks_url(value: Any) -> str:
    raw = _text(value)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return WATERLOOWORKS_ORIGIN
    if parts.scheme not in {"http", "https"} or parts.hostname != "waterlooworks.uwaterloo.ca":
        return WATERLOOWORKS_ORIGIN
    return urlunsplit(("https", "waterlooworks.uwaterloo.ca", parts.path, "", ""))


def _posting_date(fields: dict[str, Any]) -> date | None:
    for label in ("Posting Date", "Date Posted"):
        value = _text(fields.get(label))
        if not value:
            continue
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
            try:
                from datetime import datetime

                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def waterlooworks_salary(raw: dict[str, Any]) -> ParsedSalary | None:
    """Extract salary the same way public postings do, from posting text."""

    overview = raw.get("overviewFields") or {}
    inputs: list[str] = []
    for field_key, field_value in overview.items():
        mapping = _RATE_FIELD_INTERVALS.get(field_key.casefold())
        if mapping:
            _, unit = mapping
            value = _text(field_value)
            if value and not _is_boilerplate(value):
                inputs.append(f"Compensation: ${value} {unit}")
    for label in _COMPENSATION_LABELS:
        for field_key, field_value in overview.items():
            if field_key.casefold() == label and _text(field_value):
                inputs.append(str(field_value))
    jd_text = str(raw.get("fullJdText") or raw.get("rowText") or "")
    if jd_text:
        inputs.append(jd_text)
    return extract_salary_from_description("\n\n".join(inputs), country_code="CA")


def _is_boilerplate(value: str) -> bool:
    lowered = value.casefold()
    return lowered in _BOILERPLATE_VALUES or any(
        lowered.startswith(prefix) for prefix in ("to be discussed", "not specified")
    )
