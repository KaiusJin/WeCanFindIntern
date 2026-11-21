import assert from "node:assert/strict";
import test from "node:test";
import { renderJobCard, renderJobDetail } from "../modules/helpers.js";

const salary = { minimum: "25", maximum: "35", interval: "hourly", currency: "CAD" };

test("WaterlooWorks rows and details show every source board, the current deadline, ID and pay", () => {
  const job = {
    source_job_id: "00123", title: "Developer", organization: "R&D",
    boards: ["campus", "contract"], board_labels: { campus: "Campus jobs", contract: "Contract jobs" },
    application_deadline: "Nov 20, 2026 9:00 AM", submitted_application_deadline: "Nov 27, 2026 9:00 AM",
    application_term: "Winter 2027", salary,
  };
  for (const render of [renderJobCard, renderJobDetail]) {
    const html = render(job, { waterlooWorks: true });
    for (const value of ["Job ID 00123", "Campus jobs", "Contract jobs", "Nov 27, 2026 9:00 AM", "CAD $25.00–$35.00/hour"]) {
      assert.ok(html.includes(value), value);
    }
    assert.doesNotMatch(html, /Winter 2027|Recruiting term|Nov 20|Posted Job ID/);
    const noBoard = render({ ...job, boards: [], submitted_application_deadline: null, salary: null }, { waterlooWorks: true });
    assert.match(noBoard, /Nov 20, 2026 9:00 AM/);
    assert.match(noBoard, /Salary not disclosed/);
    assert.doesNotMatch(noBoard, /All boards|Campus jobs|Contract jobs/);
  }
});

test("public rows and details retain the recruiting term and share the salary format", () => {
  const job = {
    id: "public-123", title: "Developer", company_name: "R&D", recruiting_term: { display_name: "Winter 2027" },
    date_posted: "2026-11-21", sources: [{ url: "https://example.com/apply" }], salary,
  };
  for (const render of [renderJobCard, renderJobDetail]) {
    const html = render(job);
    assert.match(html, /Winter 2027/);
    assert.ok(html.includes("CAD $25.00–$35.00/hour"));
    assert.doesNotMatch(html, /Job ID|Source board|Application deadline/);
  }
  assert.match(renderJobDetail(job), /href="https:\/\/example\.com\/apply"/);
});
