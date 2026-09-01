"""Strict resume validation and safe text extraction for hostile uploads."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_LATEX_BYTES = 1024 * 1024
MAX_PDF_PAGES = 20
MAX_EXTRACTED_CHARS = 250_000

PDF_MIME_TYPES = {"application/pdf", "application/octet-stream", ""}
LATEX_MIME_TYPES = {
    "application/x-tex",
    "text/x-tex",
    "text/plain",
    "application/octet-stream",
    "",
}
DANGEROUS_LATEX = re.compile(
    r"\\(?:write18|immediate\s*\\write18|input|include|openin|openout|read|write|"
    r"directlua|catcode|csname|usepackage\s*\{(?:shellesc|catchfile|verbatim))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ValidatedResume:
    source_type: str
    media_type: str
    extracted_text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedPdfText:
    text: str
    page_texts: tuple[str, ...]


def _safe_filename(filename: str | None) -> str:
    if not filename:
        raise ValueError("A filename is required.")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("The filename must not contain a path.")
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise ValueError("The filename is invalid.")
    return filename


def _require_english(text: str) -> None:
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 120:
        raise ValueError("The resume does not contain enough extractable text.")
    latin = sum("A" <= char <= "Z" or "a" <= char <= "z" for char in letters)
    if latin / len(letters) < 0.85:
        raise ValueError("Only English resumes are supported.")
    normalized_text = re.sub(r"\s+", " ", text.lower())
    lowered = f" {normalized_text} "
    anchors = (" education ", " experience ", " skills ", " project", " university")
    if sum(anchor in lowered for anchor in anchors) < 2:
        raise ValueError("The uploaded content does not look like an English resume.")


def _reject_pdf_active_content(reader: PdfReader) -> None:
    root = reader.trailer.get("/Root")
    if not root:
        raise ValueError("The PDF catalog is missing.")
    catalog = root.get_object()
    if {"/AA", "/AcroForm"}.intersection(catalog.keys()):
        raise ValueError("Interactive or automatically executed PDF content is not accepted.")
    open_action = catalog.get("/OpenAction")
    if open_action:
        resolved_open_action = open_action.get_object()
        # OpenAction may be a plain destination (an array/name/string) or a
        # GoTo action. Both only select the initial page/view. Any other action
        # type can execute code, launch a program, submit data, or reach an
        # external resource and is rejected.
        if hasattr(resolved_open_action, "keys") and (
            resolved_open_action.get("/S") != "/GoTo"
            or "/D" not in resolved_open_action
        ):
            raise ValueError("Unsafe PDF open action was rejected.")
    names = catalog.get("/Names")
    if names and {"/JavaScript", "/EmbeddedFiles"}.intersection(names.get_object().keys()):
        raise ValueError("PDF JavaScript and embedded files are not accepted.")
    for page in reader.pages:
        if "/AA" in page:
            raise ValueError("PDF page actions are not accepted.")
        for annotation_ref in page.get("/Annots", []):
            annotation = annotation_ref.get_object()
            if annotation.get("/Subtype") != "/Link":
                raise ValueError("Only ordinary web links are allowed in PDF annotations.")
            if "/Dest" in annotation:
                # A direct destination is an internal page/bookmark link.
                continue
            action_ref = annotation.get("/A")
            action = action_ref.get_object() if action_ref else None
            if not action:
                raise ValueError("Unsafe PDF link action was rejected.")
            action_type = action.get("/S")
            if action_type == "/GoTo" and "/D" in action:
                # An explicit GoTo action is also an internal page/bookmark link.
                continue
            uri = str(action.get("/URI", ""))
            if action_type != "/URI" or not re.match(
                r"^(?:https?://|mailto:)", uri, re.IGNORECASE
            ):
                raise ValueError("Unsafe PDF link action was rejected.")


def _extract_pdf(content: bytes) -> ValidatedResume:
    if len(content) > MAX_PDF_BYTES:
        raise ValueError("PDF files must be 8 MB or smaller.")
    if not content.startswith(b"%PDF-"):
        raise ValueError("The file extension is .pdf but the content is not a PDF.")
    if b"%%EOF" not in content[-4096:]:
        raise ValueError("The PDF is incomplete or has an invalid trailer.")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
    except Exception as exc:
        raise ValueError("The PDF is malformed and cannot be read safely.") from exc
    if reader.is_encrypted:
        raise ValueError("Password-protected PDFs are not supported.")
    if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
        raise ValueError(f"PDF resumes must contain between 1 and {MAX_PDF_PAGES} pages.")
    _reject_pdf_active_content(reader)
    try:
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:
        raise ValueError("Text extraction from the PDF failed.") from exc
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError("The PDF expands to too much text.")
    if len(re.sub(r"\s+", "", text)) < 200:
        raise ValueError("Image-only and scanned PDFs are not supported.")
    _require_english(text)
    return ValidatedResume("pdf", "application/pdf", text)


def extract_pdf_text(
    filename: str | None, declared_content_type: str | None, content: bytes
) -> ExtractedPdfText:
    """Extract text from a safe text PDF and append ordinary web links."""
    safe_name = _safe_filename(filename)
    if Path(safe_name).suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are accepted.")
    media_type = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in PDF_MIME_TYPES:
        raise ValueError("The declared file type does not match a PDF.")
    if len(content) > MAX_PDF_BYTES:
        raise ValueError("PDF files must be 8 MB or smaller.")
    if not content.startswith(b"%PDF-"):
        raise ValueError("The file extension is .pdf but the content is not a PDF.")
    if b"%%EOF" not in content[-4096:]:
        raise ValueError("The PDF is incomplete or has an invalid trailer.")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
    except Exception as exc:
        raise ValueError("The PDF is malformed and cannot be read safely.") from exc
    if reader.is_encrypted:
        raise ValueError("Password-protected PDFs are not supported.")
    if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
        raise ValueError(f"PDF files must contain between 1 and {MAX_PDF_PAGES} pages.")
    _reject_pdf_active_content(reader)
    try:
        page_texts = tuple((page.extract_text() or "") for page in reader.pages)
        text = "\n\n".join(page_texts).strip()
    except Exception as exc:
        raise ValueError("Text extraction from the PDF failed.") from exc
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError("The PDF expands to too much text.")
    if len(re.sub(r"\s+", "", text)) < 20:
        raise ValueError("A text-based PDF is required; image-only PDFs are not supported.")

    links: list[str] = []
    for page in reader.pages:
        for annotation_ref in page.get("/Annots", []):
            annotation = annotation_ref.get_object()
            if annotation.get("/Subtype") != "/Link":
                continue
            action_ref = annotation.get("/A")
            action = action_ref.get_object() if action_ref else None
            if action and action.get("/S") == "/URI":
                uri = str(action.get("/URI", ""))
                if re.match(r"^(?:https?://|mailto:)", uri, re.IGNORECASE):
                    links.append(uri)
    missing_links = list(dict.fromkeys(link for link in links if link not in text))
    if missing_links:
        text += "\n\nLinks:\n" + "\n".join(missing_links)
    return ExtractedPdfText(text=text, page_texts=page_texts)


def extract_text_pdf_plain(
    filename: str | None, declared_content_type: str | None, content: bytes
) -> str:
    """Compatibility wrapper for callers that only need flattened text."""

    return extract_pdf_text(filename, declared_content_type, content).text


def _strip_latex(source: str) -> str:
    source = re.sub(r"(?<!\\)%.*", "", source)
    source = re.sub(r"\\documentclass(?:\[[^\]]*\])?\s*\{[^{}]*\}", "", source)
    source = re.sub(r"\\(?:href|url)\s*\{([^{}]*)\}(?:\{([^{}]*)\})?", r" \2 \1 ", source)
    source = re.sub(
        r"\\(?:section\*?|subsection\*?|textbf|textit|emph|small|large|Large|item)"
        r"\s*\{([^{}]*)\}",
        r"\n\1\n",
        source,
    )
    source = re.sub(r"\\begin\s*\{[^{}]+\}|\\end\s*\{[^{}]+\}", "\n", source)
    source = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?", " ", source)
    source = source.replace("\\&", "&").replace("\\%", "%").replace("~", " ")
    source = source.replace("{", " ").replace("}", " ")
    lines = [re.sub(r"\s+", " ", line).strip(" -&|") for line in source.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _extract_latex(content: bytes) -> ValidatedResume:
    if len(content) > MAX_LATEX_BYTES:
        raise ValueError("LaTeX resume files must be 1 MB or smaller.")
    if content.startswith(b"%PDF-"):
        raise ValueError("The file extension is .tex but the content is a PDF.")
    if b"\x00" in content:
        raise ValueError("Binary content is not accepted as LaTeX source.")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("LaTeX source must be valid UTF-8 text.") from exc
    if DANGEROUS_LATEX.search(source):
        raise ValueError("The LaTeX source contains file access or executable commands.")
    if not re.search(r"\\(?:documentclass|begin\s*\{document\}|section\*?\s*\{)", source):
        raise ValueError("The .tex file does not look like LaTeX resume source.")
    text = _strip_latex(source)
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError("The LaTeX source expands to too much text.")
    _require_english(text)
    return ValidatedResume(
        "latex",
        "application/x-tex",
        text,
        ("LaTeX source is parsed as text and is never compiled or executed.",),
    )


def validate_and_extract_resume(
    filename: str | None, declared_content_type: str | None, content: bytes
) -> ValidatedResume:
    """Validate extension, MIME declaration, magic bytes, structure and language."""
    safe_name = _safe_filename(filename)
    if not content:
        raise ValueError("The uploaded resume is empty.")
    extension = Path(safe_name).suffix.lower()
    media_type = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if extension == ".pdf":
        if media_type not in PDF_MIME_TYPES:
            raise ValueError("The declared file type does not match a PDF resume.")
        return _extract_pdf(content)
    if extension == ".tex":
        if media_type not in LATEX_MIME_TYPES:
            raise ValueError("The declared file type does not match LaTeX source.")
        return _extract_latex(content)
    raise ValueError("Only .pdf and .tex English resumes are supported.")
