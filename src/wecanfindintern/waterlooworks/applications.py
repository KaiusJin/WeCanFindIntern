"""WaterlooWorks submitted-application extraction."""

from __future__ import annotations

from .browser_scripts import WATERLOOWORKS_EXTRACTION_HELPERS
from .config import WATERLOOWORKS_ORIGIN
from .dates import parse_waterlooworks_date, parse_waterlooworks_datetime

__all__ = [
    "EXTRACT_APPLICATIONS_SCRIPT",
    "OPEN_TOTAL_SUBMITTED_SCRIPT",
    "WATERLOOWORKS_APPLICATIONS_URL",
    "parse_waterlooworks_date",
    "parse_waterlooworks_datetime",
]

WATERLOOWORKS_APPLICATIONS_URL = (
    f"{WATERLOOWORKS_ORIGIN}/myAccount/co-op/full/applications.htm"
)


# This script runs only after Total Submitted has been opened. It reads every
# paginated row and calls getPostingData/getPostingOverview directly. A
# per-posting failure retains the verified list fields as a fallback.
OPEN_TOTAL_SUBMITTED_SCRIPT = r"""
(() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const row = [...document.querySelectorAll("table tr")].find((candidate) =>
    clean(candidate.querySelector("th,td")?.textContent).toLowerCase() ===
      "total submitted:"
  );
  const view = row?.querySelector("button,a");
  if (!view) throw new Error("Total Submitted View control was not found.");
  view.click();
  return true;
})()
"""


EXTRACT_APPLICATIONS_SCRIPT = WATERLOOWORKS_EXTRACTION_HELPERS + r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function applicationsTable() {
    return [...document.querySelectorAll("table")].find((table) => {
      const text = cleanText(table.querySelector("thead")?.innerText);
      return text.includes("Job ID") && text.includes("App Status") &&
        text.includes("App Submitted On");
    });
  }

  async function waitForApplicationsTable() {
    for (let i = 0; i < 80; i += 1) {
      const table = applicationsTable();
      if (table?.querySelector("tbody tr")) return table;
      await sleep(250);
    }
    throw new Error("Total Submitted did not open the applications table.");
  }

  await waitForApplicationsTable();

  function pageRows() {
    const table = applicationsTable();
    const headers = [...table.querySelectorAll("thead th")].map((cell) =>
      cleanText(cell.innerText).replace(/\s+(?:swap_vert|keyboard_arrow_down)$/i, "")
    );
    return [...table.querySelectorAll("tbody tr")].map((row) => {
      const cells = [...row.querySelectorAll("th,td")];
      const values = Object.fromEntries(headers.map((header, index) => [
        header, cleanText(cells[index]?.innerText)
      ]));
      const titleLink = cells[0]?.querySelector("a");
      return {
        element: row,
        titleLink,
        id: values["Job ID"],
        title: cleanText(titleLink?.innerText),
        term: values["Term"],
        organization: values["Organization"],
        appStatus: values["App Status"],
        jobStatus: values["Job Status"],
        division: values["Division"],
        region: values["Location"],
        city: values["City"],
        openings: values["Openings"],
        applicationDeadline: values["App Deadline"],
        submittedAt: values["App Submitted On (1)"] || values["App Submitted On"],
        submittedBy: values["App Submitted By"],
      };
    }).filter((row) => row.id && row.title && row.titleLink);
  }

  async function detailFor(row) {
    const [metadata, overviewHtml] = await Promise.all([
      callWW(getPostingData, row.id, "getPostingData"),
      callWW(getPostingOverview, row.id, "getPostingOverview"),
    ]);
    const doc = new DOMParser().parseFromString(String(overviewHtml || ""), "text/html");
    const parsed = parseOverview(doc);
    const geo = metadata.geoData || {};
    const app = metadata.applicationData || {};
    return {
      overviewFields: parsed.fields,
      overviewLists: parsed.lists,
      fullJdText: htmlToText(doc.body),
      location: {
        address: geo.address || "",
        city: geo.city || parsed.fields["Job - City"] || row.city || "",
        province: geo.province || parsed.fields["Job - Province/State"] || row.region || "",
        country: geo.country || parsed.fields["Job - Country"] || "",
        postalCode: geo.postalCode || "",
        latitude: geo.latitude ?? null,
        longitude: geo.longitude ?? null,
      },
      application: {
        deadline: parsed.fields["Application Deadline"] || row.applicationDeadline || "",
        documentsRequired: parsed.fields["Application Documents Required"] || "",
        delivery: parsed.fields["Application Method"] || "",
        canApply: app.canApply ?? null,
        hasApplication: app.hasApplication ?? null,
      },
      organization: metadata.org || parsed.fields["Organization"] || row.organization,
      division: metadata.div || parsed.fields["Division"] || row.division,
      title: metadata.position || row.title,
    };
  }

  if (typeof getPostingData !== "function" || typeof getPostingOverview !== "function") {
    throw new Error("WaterlooWorks posting detail APIs are unavailable on Applications.");
  }

  const applications = [];
  const seen = new Set();
  let pageCount = 0;
  while (true) {
    pageCount += 1;
    const rows = pageRows();
    const firstId = rows[0]?.id;
    for (let offset = 0; offset < rows.length; offset += 3) {
      const batch = rows.slice(offset, offset + 3);
      const details = await Promise.all(batch.map(async (row) => {
        const record = {
          source: "waterlooworks",
          sourceUrl: location.href,
          id: row.id,
          title: row.title,
          organization: row.organization,
          division: row.division,
          applicationRecord: {
            term: row.term,
            appStatus: row.appStatus,
            jobStatus: row.jobStatus,
            openings: row.openings,
            applicationDeadline: row.applicationDeadline,
            submittedAt: row.submittedAt,
            submittedBy: row.submittedBy,
          },
        };
        try {
          Object.assign(record, await detailFor(row));
        } catch (error) {
          record.location = {city: row.city, province: row.region, country: "", address: ""};
          record.application = {deadline: row.applicationDeadline};
          record.detailError = String(error);
        }
        return record;
      }));
      for (const record of details) {
        if (seen.has(record.id)) continue;
        seen.add(record.id);
        applications.push(record);
      }
    }
    const next = document.querySelector("a[aria-label='Go to next page']");
    if (!next || next.classList.contains("disabled") || next.closest(".disabled")) break;
    next.click();
    let changed = false;
    for (let i = 0; i < 80; i += 1) {
      await sleep(250);
      const nextId = pageRows()[0]?.id;
      if (nextId && nextId !== firstId) { changed = true; break; }
    }
    if (!changed) break;
  }
  return {
    generatedAt: new Date().toISOString(),
    pageCount,
    count: applications.length,
    applications,
  };
})()
"""
