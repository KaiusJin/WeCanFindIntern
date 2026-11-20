const TRACKER_STAGE_LABELS = {
  interested: "Interested",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
};

const TRACKER_SOURCE_LABELS = {
  wecanfindintern: "WeCanFindIntern",
  linkedin: "LinkedIn",
  indeed: "Indeed",
  glassdoor: "Glassdoor",
  zip_recruiter: "ZipRecruiter",
  google: "Google Jobs",
  waterloo_work: "WaterlooWorks",
  other: "Other",
};

let trackerContractPromise = null;

function replaceContract(target, values) {
  Object.keys(target).forEach((key) => { delete target[key]; });
  Object.assign(target, values);
}

function loadTrackerContract() {
  if (!trackerContractPromise) {
    trackerContractPromise = fetch("/api/v1/tracker/contract")
      .then((response) => {
        if (!response.ok) throw new Error("Could not load Tracker contract");
        return response.json();
      })
      .then((contract) => {
        if (contract.stages && Object.keys(contract.stages).length) {
          replaceContract(TRACKER_STAGE_LABELS, contract.stages);
        }
        if (contract.sources && Object.keys(contract.sources).length) {
          replaceContract(TRACKER_SOURCE_LABELS, contract.sources);
        }
        return contract;
      })
      .catch((error) => {
        trackerContractPromise = null;
        throw error;
      });
  }
  return trackerContractPromise;
}

function trackerStageLabel(stage) {
  return TRACKER_STAGE_LABELS[stage]
    || String(stage || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function trackerStageOptions(selected = "", { placeholder = null } = {}) {
  const placeholderOption = placeholder == null
    ? ""
    : `<option value="">${placeholder}</option>`;
  return placeholderOption + Object.entries(TRACKER_STAGE_LABELS)
    .map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`)
    .join("");
}

function trackerSourceOptions(selected = "other") {
  return Object.entries(TRACKER_SOURCE_LABELS)
    .map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`)
    .join("");
}

export {
  TRACKER_SOURCE_LABELS,
  TRACKER_STAGE_LABELS,
  loadTrackerContract,
  trackerSourceOptions,
  trackerStageLabel,
  trackerStageOptions,
};
