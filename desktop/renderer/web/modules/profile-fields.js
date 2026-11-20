import { resumeContactUrl } from "./helpers.js?v=20260901-error-dialog-minimal-v1";

export function profileFieldValue(input) {
  const value = input.value.trim();
  if (input.dataset.profileType === "list") return value.split(",").map(part => part.trim()).filter(Boolean);
  if (input.dataset.profileType === "lines") return value.split("\n").map(part => part.trim()).filter(Boolean);
  // Keep a pending invalid edit when another entry is added or removed. Saving validates first.
  if (input.inputMode === "url" && value) return resumeContactUrl(value) || value;
  return value || null;
}

export function profileFormatError(input) {
  const value = input.value.trim();
  if (!value) return "";
  if (input.type === "email" && input.validity.typeMismatch) {
    return "Enter an email address, such as name@example.com.";
  }
  if (input.inputMode === "url" && !resumeContactUrl(value)) {
    return "Enter a website address, such as github.com/your-name.";
  }
  return "";
}
