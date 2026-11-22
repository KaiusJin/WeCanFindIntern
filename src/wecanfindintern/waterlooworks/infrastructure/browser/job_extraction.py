"""Authenticated WaterlooWorks board API extraction inside Chrome."""

from __future__ import annotations

import json
from collections.abc import Iterable

from wecanfindintern.waterlooworks.infrastructure.browser.scripts import (
    WATERLOOWORKS_EXTRACTION_HELPERS,
)

# This runs inside the authenticated WaterlooWorks board page. It discovers the
# session-specific POST action tokens embedded by WaterlooWorks and calls those
# same-origin APIs directly. No responsive table/card DOM or simulated clicks are
# involved. Authentication remains entirely in Chrome; only extracted records cross
# into the app.
_EXTRACT_JOBS_SCRIPT_TEMPLATE = (
    WATERLOOWORKS_EXTRACTION_HELPERS
    + r"""
(async () => {
  const ITEMS_PER_PAGE = 100;
  const DETAIL_BATCH_SIZE = 6;
  const knownIds = new Set(__KNOWN_JOB_IDS__.map(String));

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
  let detailCount = 0;
  let reusedCount = 0;
  let page = 0;
  while (true) {
    page += 1;
    const listResult = await listPage(actions, page);
    const rows = listResult.data.map(listRow).filter(Boolean);
    const previousCount = seen.size;
    const detailRows = [];
    for (const row of rows) {
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      if (knownIds.has(String(row.id))) {
        jobs.push({source: "waterlooworks", ...row, known: true});
        reusedCount += 1;
      } else {
        detailRows.push(row);
      }
    }
    for (let i = 0; i < detailRows.length; i += DETAIL_BATCH_SIZE) {
      const results = await Promise.all(
        detailRows.slice(i, i + DETAIL_BATCH_SIZE).map((row) => fetchOne(actions, row))
      );
      for (const job of results) {
        jobs.push(job);
        detailCount += 1;
      }
    }
    const totalResults = Number(listResult.totalResults);
    const reachedTotal = Number.isFinite(totalResults) && seen.size >= totalResults;
    if (!rows.length || seen.size === previousCount || reachedTotal ||
        rows.length < ITEMS_PER_PAGE) break;
  }
  return {
    generatedAt: new Date().toISOString(),
    pageCount: page,
    count: jobs.length,
    detailCount,
    reusedCount,
    jobs,
  };
})()
"""
)


def build_extract_jobs_script(known_job_ids: Iterable[str]) -> str:
    """Build the board extractor while marking immutable local jobs as reusable."""

    encoded_ids = json.dumps(
        sorted({str(job_id) for job_id in known_job_ids}),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return _EXTRACT_JOBS_SCRIPT_TEMPLATE.replace("__KNOWN_JOB_IDS__", encoded_ids)


EXTRACT_JOBS_SCRIPT = build_extract_jobs_script(())
