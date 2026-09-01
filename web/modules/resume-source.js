export async function extractResumePdf(file) {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/v1/resumes/extract-pdf", { method: "POST", body: form });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.ok || !result.text) {
    throw new Error(result.detail || result.error || "Resume extraction failed.");
  }
  return result;
}

export async function loadProfileContext() {
  const response = await fetch("/api/v1/profile/context");
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || "Could not load Profile.");
  return result;
}
