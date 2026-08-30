import { $, escapeHtml } from "./helpers.js";
import { switchTab } from "./navigation.js";
import { applyRegionFilter } from "./jobs.js";

// =========================================================
// JOB DISTRIBUTION HEATMAP — real choropleth (d3-geo).
// US: us-atlas TopoJSON + geoAlbersUsa; CA: vendored Natural
// Earth provinces with geoMercator. Click a region to open the
// Jobs tab filtered to it.
// =========================================================

const COLOR_LOW = [238, 245, 238];
const COLOR_HIGH = [30, 75, 56];

const US_NAME_TO_CODE = {
  Alabama: "AL", Alaska: "AK", Arizona: "AZ", Arkansas: "AR",
  California: "CA", Colorado: "CO", Connecticut: "CT", Delaware: "DE",
  "District of Columbia": "DC", Florida: "FL", Georgia: "GA", Hawaii: "HI",
  Idaho: "ID", Illinois: "IL", Indiana: "IN", Iowa: "IA", Kansas: "KS",
  Kentucky: "KY", Louisiana: "LA", Maine: "ME", Maryland: "MD",
  Massachusetts: "MA", Michigan: "MI", Minnesota: "MN", Mississippi: "MS",
  Missouri: "MO", Montana: "MT", Nebraska: "NE", Nevada: "NV",
  "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
  "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
  Ohio: "OH", Oklahoma: "OK", Oregon: "OR", Pennsylvania: "PA",
  "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
  Tennessee: "TN", Texas: "TX", Utah: "UT", Vermont: "VT", Virginia: "VA",
  Washington: "WA", "West Virginia": "WV", Wisconsin: "WI", Wyoming: "WY",
};

function heatColor(count, maxCount) {
  if (!count) return "#f4f8f4";
  const t = Math.sqrt(count) / Math.sqrt(maxCount || 1);
  const channel = (index) =>
    Math.round(COLOR_LOW[index] + (COLOR_HIGH[index] - COLOR_LOW[index]) * t);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      existing.addEventListener("load", resolve);
      existing.addEventListener("error", reject);
      if (existing.dataset.loaded === "true") resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(script);
  });
}

async function ensureD3() {
  await loadScript("/vendor/d3.min.js");
  await loadScript("/vendor/topojson-client.min.js");
}

function showTooltip(event, text) {
  const tooltip = $("#heatmap-tooltip");
  if (!tooltip) return;
  tooltip.hidden = false;
  tooltip.textContent = text;
  const bounds = tooltip.parentElement.getBoundingClientRect();
  tooltip.style.left = `${event.clientX - bounds.left + 14}px`;
  tooltip.style.top = `${event.clientY - bounds.top + 14}px`;
}

function hideTooltip() {
  const tooltip = $("#heatmap-tooltip");
  if (tooltip) tooltip.hidden = true;
}

function renderUsMap(states, counts, nameLookup, maxCount) {
  const container = $("#heatmap-us");
  if (!container) return;
  const width = 960;
  const height = 600;
  const projection = d3.geoAlbersUsa().fitSize([width, height], states);
  const path = d3.geoPath(projection);

  const svg = d3
    .select(container)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "United States job distribution map");

  const country = "US";
  svg
    .selectAll("path")
    .data(states.features)
    .join("path")
    .attr("d", path)
    .attr("fill", (feature) => {
      const code = US_NAME_TO_CODE[feature.properties.name];
      return heatColor(code ? counts[`${country}:${code}`] || 0 : 0, maxCount);
    })
    .attr("stroke", "#ffffff")
    .attr("stroke-width", 0.7)
    .attr("class", (feature) =>
      US_NAME_TO_CODE[feature.properties.name] ? "heatmap-region" : "heatmap-region heatmap-region-disabled",
    )
    .each(function (feature) {
      const code = US_NAME_TO_CODE[feature.properties.name];
      this.__country = country;
      this.__regionCode = code || null;
      this.__regionName = feature.properties.name;
      this.__regionCount = code ? counts[`${country}:${code}`] || 0 : 0;
    })
    .call(attachRegionSelection);
}

// d3 .call helper: hover tooltip + click-through to the filtered Jobs tab.
function attachRegionSelection(selection) {
  selection
    .attr("tabindex", 0)
    .on("mousemove", function (event) {
      showTooltip(event, `${this.__regionName} — ${this.__regionCount} jobs`);
      d3.select(this).attr("stroke-width", 1.6);
    })
    .on("mouseleave", function () {
      hideTooltip();
      d3.select(this).attr("stroke-width", 0.7);
    })
    .on("click", function () {
      if (!this.__regionCode) return;
      applyRegionFilter(this.__country, this.__regionCode);
      switchTab("tab-jobs");
    });
}

function renderCaMap(geojson, counts, nameLookup, maxCount) {
  const container = $("#heatmap-ca");
  if (!container) return;
  const width = 760;
  const height = 560;
  const projection = d3.geoMercator().fitSize([width, height], geojson);
  const path = d3.geoPath(projection);

  const svg = d3
    .select(container)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Canada job distribution map");

  svg
    .selectAll("path")
    .data(geojson.features)
    .join("path")
    .attr("d", path)
    .attr("fill", (feature) =>
      heatColor(counts[`CA:${feature.properties.code}`] || 0, maxCount),
    )
    .attr("stroke", "#ffffff")
    .attr("stroke-width", 0.7)
    .attr("class", "heatmap-region")
    .each(function (feature) {
      this.__country = "CA";
      this.__regionCode = feature.properties.code;
      this.__regionName = feature.properties.name;
      this.__regionCount = counts[`CA:${feature.properties.code}`] || 0;
    })
    .call(attachRegionSelection);
}

async function loadHeatmap() {
  try {
    const response = await fetch("/api/v1/jobs/geo-distribution");
    if (!response.ok) throw new Error("Could not load distribution data.");
    const data = await response.json();

    const counts = {};
    const nameLookup = { US: {}, CA: {} };
    const regionCount = { US: 0, CA: 0 };
    for (const region of data.regions) {
      counts[`${region.country}:${region.region_code}`] = region.count;
      nameLookup[region.country][region.region_code] = region.region_name;
      regionCount[region.country] += 1;
    }
    const maxCount = data.regions.length ? data.regions[0].count : 0;

    $("#heatmap-us-total").textContent = `${data.by_country?.US ?? 0} jobs`;
    $("#heatmap-ca-total").textContent = `${data.by_country?.CA ?? 0} jobs`;
    $("#heatmap-legend-min").textContent = "0";
    $("#heatmap-legend-max").textContent = `${maxCount}+`;
    $("#heatmap-legend-note").textContent =
      "Color intensity follows a square-root scale of active postings. Click any state or province to open the Jobs tab filtered to that region.";

    renderTopList(data.regions, data.total);
    renderCountrySummary(data.by_country ?? {}, regionCount);

    await ensureD3();
    const usTopo = await (await fetch("/vendor/us-states-10m.json")).json();
    const caGeo = await (await fetch("/vendor/canada-provinces.geojson")).json();
    renderUsMap(topojson.feature(usTopo, usTopo.objects.states), counts, nameLookup, maxCount);
    renderCaMap(caGeo, counts, nameLookup, maxCount);
  } catch (err) {
    const us = $("#heatmap-us");
    if (us) {
      us.innerHTML = `<p class="muted-copy">Heatmap unavailable: ${escapeHtml(err.message)}</p>`;
    }
  }
}

function renderTopList(regions, total) {
  const list = $("#heatmap-top-list");
  if (!list) return;
  const top = regions.slice(0, 12);
  list.innerHTML = top
    .map((region) => {
      const width = total ? Math.round((region.count / total) * 100) : 0;
      const flag = region.country === "CA" ? "CA" : "US";
      return `
      <div class="heatmap-top-row">
        <span class="heatmap-top-country">${flag}</span>
        <span class="heatmap-top-name">${escapeHtml(region.region_name)}</span>
        <div class="heatmap-top-bar"><span style="width: ${width}%"></span></div>
        <span class="heatmap-top-count">${region.count}</span>
      </div>`;
    })
    .join("");
}

function renderCountrySummary(byCountry, countryRegionCounts) {
  const wrap = $("#heatmap-country-summary");
  if (!wrap) return;
  wrap.innerHTML = `
    <p><strong>Canada</strong> — ${byCountry.CA ?? 0} jobs across ${countryRegionCounts.CA} provinces/territories</p>
    <p><strong>United States</strong> — ${byCountry.US ?? 0} jobs across ${countryRegionCounts.US} states/districts</p>`;
}

loadHeatmap();
