"""Shared JavaScript helpers evaluated inside authenticated WaterlooWorks pages."""

WATERLOOWORKS_API_READINESS_SCRIPT = r"""
(() => {
  const source = [...document.scripts]
    .map((script) => script.textContent || "")
    .join("\n");
  return {
    path: location.pathname,
    authenticated: location.pathname.startsWith("/myAccount/"),
    ready: document.readyState !== "loading" &&
      typeof fetch === "function" &&
      /dataParams\s*:\s*\{\s*action\s*:\s*['"][^'"]+/m.test(source) &&
      /function\s+getPostingData\s*\([^)]*\)[\s\S]*?action\s*:\s*['"][^'"]+/m
        .test(source) &&
      /function\s+getPostingOverview\s*\([^)]*\)[\s\S]*?action\s*:\s*['"][^'"]+/m
        .test(source),
  };
})()
"""

WATERLOOWORKS_EXTRACTION_HELPERS = r"""
function callWW(fn, id, name, timeout = 20000) {
  return new Promise((resolve, reject) => {
    let finished = false;
    const timer = setTimeout(() => {
      if (!finished) {
        finished = true;
        reject(new Error(`${name}(${id}) timeout`));
      }
    }, timeout);
    try {
      fn(id, (data) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        data == null
          ? reject(new Error(`${name}(${id}) returned empty`))
          : resolve(data);
      });
    } catch (error) {
      clearTimeout(timer);
      reject(error);
    }
  });
}

function cleanText(value) {
  return String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function extractionRoot(value) {
  if (!value) return null;
  if (typeof value === "string") {
    return new DOMParser().parseFromString(value, "text/html").body;
  }
  return value;
}

function htmlToText(value) {
  const root = extractionRoot(value);
  if (!root) return "";
  const clone = root.cloneNode(true);
  clone.querySelectorAll("script,style,button,input,select,textarea")
    .forEach((el) => el.remove());
  clone.querySelectorAll("br").forEach((el) => el.replaceWith("\n"));
  clone.querySelectorAll("li").forEach((el) => {
    el.insertBefore(document.createTextNode("\n- "), el.firstChild);
  });
  clone.querySelectorAll("tr").forEach((el) => {
    el.appendChild(document.createTextNode("\n"));
  });
  return cleanText(clone.innerText || clone.textContent);
}

function parseOverview(value) {
  const root = extractionRoot(value);
  const fields = {};
  const lists = {};
  if (!root) return { fields, lists };
  root.querySelectorAll(".tag__key-value-list.js--question--container")
    .forEach((container) => {
      const labelElement = container.querySelector(".label");
      if (!labelElement) return;
      const label = cleanText(labelElement.textContent).replace(/:$/, "").trim();
      const clone = container.cloneNode(true);
      clone.querySelector(".label")?.remove();
      clone.querySelectorAll("br").forEach((el) => el.replaceWith("\n"));
      clone.querySelectorAll("li").forEach((el) => {
        el.insertBefore(document.createTextNode("\n- "), el.firstChild);
      });
      clone.querySelectorAll("tr").forEach((el) => {
        el.appendChild(document.createTextNode("\n"));
      });
      const valueText = cleanText(clone.innerText || clone.textContent);
      if (label && valueText) fields[label] = valueText;
      const items = [...container.querySelectorAll("li")]
        .map((item) => cleanText(item.textContent))
        .filter(Boolean);
      if (items.length) lists[label] = items;
    });
  return { fields, lists };
}
"""
