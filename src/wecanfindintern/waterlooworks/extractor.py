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

# This runs inside the authenticated WaterlooWorks board page. It discovers the
# session-specific POST action tokens embedded by WaterlooWorks and calls those
# same-origin APIs directly. No responsive table/card DOM or simulated clicks are
# involved. Authentication remains entirely in Chrome; only extracted records cross
# into the app.
EXTRACT_JOBS_SCRIPT = WATERLOOWORKS_EXTRACTION_HELPERS + r"""
(async () => {
  const ITEMS_PER_PAGE = 100;

  function inlineSource() {
    return [...document.scripts]
      .map((script) => script.textContent || "")
      .join("\n");
  }

  function actionForFunction(source, name) {
    const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = source.match(new RegExp(
      `function\\s+${escapedName}\\s*\\([^)]*\\)[\\s\\S]*?` +
      `action\\s*:\\s*['\"]([^'\"]+)`,
      "m"
    ));
    if (!match) throw new Error(`WaterlooWorks ${name} API action was not found.`);
    return match[1];
  }

  function apiContract() {
    const source = inlineSource();
    const listMatch = source.match(
      /dataParams\s*:\s*\{\s*action\s*:\s*['"]([^'"]+)/m
    );
    if (!listMatch) {
      throw new Error("WaterlooWorks job-list API action was not found.");
    }
    return {
      list: listMatch[1],
      posting: actionForFunction(source, "getPostingData"),
      overview: actionForFunction(source, "getPostingOverview"),
    };
  }

  async function apiPost(action, values, responseType, timeout = 30000) {
    const body = new URLSearchParams();
    body.set("action", action);
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) continue;
      body.set(key, value !== null && typeof value === "object"
        ? JSON.stringify(value)
        : String(value));
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(location.pathname, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
          "X-Requested-With": "XMLHttpRequest",
        },
        body,
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`WaterlooWorks API returned HTTP ${response.status}.`);
      }
      const contentType = response.headers.get("content-type") || "";
      const charset = contentType.match(/charset\s*=\s*["']?([^;"'\s]+)/i)?.[1] ||
        "utf-8";
      const bytes = await response.arrayBuffer();
      let text;
      try {
        text = new TextDecoder(charset).decode(bytes);
      } catch (_) {
        text = new TextDecoder("utf-8").decode(bytes);
      }
      if (responseType === "text") {
        try {
          const decoded = JSON.parse(text);
          return typeof decoded === "string" ? decoded : text;
        } catch (_) {
          return text;
        }
      }
      try {
        return JSON.parse(text);
      } catch (_) {
        throw new Error(
          "WaterlooWorks API returned a non-JSON response; " +
          "sign-in may have expired."
        );
      }
    } finally {
      clearTimeout(timer);
    }
  }

  function listRow(raw) {
    const data = raw?.data || {};
    const titleValue = data.JobTitle?.value;
    const title = cleanText(
      titleValue?.postingTitle || titleValue?.display || titleValue ||
      data.JobTitle?.display || ""
    );
    const id = Number(raw?.id || data.Id?.value || data.ID?.value);
    if (!Number.isFinite(id)) return null;
    const rowText = cleanText(Object.values(data).map((entry) =>
      entry?.display ?? entry?.value ?? ""
    ).join(" "));
    return {id, title, rowText, sourceUrl: location.href};
  }

  async function listPage(actions, page) {
    const result = await apiPost(actions.list, {
      page,
      sort: [{key: "Id", direction: "desc"}],
      itemsPerPage: ITEMS_PER_PAGE,
      filters: "null",
      columns: [],
      keyword: "",
      isDataViewer: true,
    }, "json");
    if (!result || !Array.isArray(result.data)) {
      throw new Error("WaterlooWorks job-list API returned an invalid result.");
    }
    return result;
  }

  async function fetchOne(actions, row) {
    try {
      const [metadata, overviewHtml] = await Promise.all([
        apiPost(actions.posting, {postingId: row.id}, "json"),
        apiPost(actions.overview, {postingId: row.id}, "text"),
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

  const actions = apiContract();
  const jobs = [];
  const seen = new Set();
  let page = 0;
  while (true) {
    page += 1;
    const listResult = await listPage(actions, page);
    const rows = listResult.data.map(listRow).filter(Boolean);
    const previousCount = seen.size;
    for (let i = 0; i < rows.length; i += 3) {
      const results = await Promise.all(
        rows.slice(i, i + 3).map((row) => fetchOne(actions, row))
      );
      for (const job of results) {
        if (!seen.has(job.id)) {
          seen.add(job.id);
          jobs.push(job);
        }
      }
    }
    const totalResults = Number(listResult.totalResults);
    const reachedTotal = Number.isFinite(totalResults) && seen.size >= totalResults;
    if (!rows.length || seen.size === previousCount || reachedTotal ||
        rows.length < ITEMS_PER_PAGE) break;
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
