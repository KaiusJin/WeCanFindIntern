from __future__ import annotations

import io

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from wecanfindintern.profile.parser import parse_resume_text
from wecanfindintern.profile.security import (
    extract_text_pdf_plain,
    validate_and_extract_resume,
)

RESUME_TEXT = """Alex Chen
alex@example.com | github.com/alexchen | linkedin.com/in/alexchen

Education
University of Waterloo | Bachelor of Computer Science | Specialization in AI | Expected May 2027

Experience
Software Engineer Intern | Acme Inc. | May 2025 - August 2025
Built a FastAPI service using Python and PostgreSQL.

Projects
Intern Finder | January 2025 - Present
Built a React and Python application with Docker and AWS.

Technical Skills
Languages: Python, JavaScript, SQL
Frameworks: React, FastAPI
Tools: Git, Docker, AWS, PostgreSQL

Certifications
AWS Certified Cloud Practitioner

Languages
English - Native; French - Intermediate

Honors and Awards
Dean's Honour List 2025
"""


def _latex_resume(body: str = RESUME_TEXT) -> bytes:
    return (
        "\\documentclass{article}\n\\begin{document}\n"
        + body.replace("&", "\\&")
        + "\n\\end{document}\n"
    ).encode()


def test_latex_resume_is_parsed_without_compilation() -> None:
    result = validate_and_extract_resume("resume.tex", "application/x-tex", _latex_resume())
    assert result.source_type == "latex"
    assert "University of Waterloo" in result.extracted_text
    assert "never compiled" in result.warnings[0]


def test_latex_file_access_commands_are_kept_as_text_without_execution() -> None:
    content = _latex_resume() + (
        b"\\href{https://example.com}{Portfolio}\n"
        b"\\input{sections/experience.tex}\n"
        b"\\write18{rm -rf /tmp/example}\n"
    )
    result = validate_and_extract_resume("resume.tex", "text/plain", content)
    assert "https://example.com" in result.extracted_text
    assert "sections/experience.tex" in result.extracted_text
    assert "rm -rf /tmp/example" in result.extracted_text
    assert all("file access or executable" not in warning for warning in result.warnings)


def test_extension_disguise_is_rejected() -> None:
    try:
        validate_and_extract_resume("resume.pdf", "application/pdf", _latex_resume())
    except ValueError as error:
        assert "content is not a PDF" in str(error)
    else:
        raise AssertionError("disguised PDF was accepted")


def test_non_english_resume_is_rejected() -> None:
    chinese = "教育\n大学专业和毕业时间\n工作经验\n软件开发项目经验和技能\n" * 40
    content = (
        f"\\documentclass{{article}}\n\\begin{{document}}\n{chinese}\\end{{document}}"
    ).encode()
    try:
        validate_and_extract_resume("resume.tex", "text/plain", content)
    except ValueError as error:
        assert "English resumes" in str(error)
    else:
        raise AssertionError("non-English resume was accepted")


def test_text_pdf_is_accepted() -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in RESUME_TEXT.splitlines():
        pdf.multi_cell(0, 5, line or " ", new_x="LMARGIN", new_y="NEXT")
    result = validate_and_extract_resume("resume.PDF", "application/pdf", bytes(pdf.output()))
    assert result.source_type == "pdf"
    assert "Technical Skills" in result.extracted_text


def _resume_pdf_with_open_action(action: DictionaryObject) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for line in RESUME_TEXT.splitlines():
        pdf.multi_cell(0, 5, line or " ", new_x="LMARGIN", new_y="NEXT")
    reader = PdfReader(io.BytesIO(bytes(pdf.output())))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.root_object[NameObject("/OpenAction")] = action
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_pdf_goto_open_action_is_accepted() -> None:
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/GoTo"),
            NameObject("/D"): TextStringObject("first-page"),
        }
    )
    result = validate_and_extract_resume(
        "resume.pdf", "application/pdf", _resume_pdf_with_open_action(action)
    )
    assert result.source_type == "pdf"


def test_pdf_javascript_open_action_is_rejected() -> None:
    action = DictionaryObject(
        {
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('unsafe')"),
        }
    )
    try:
        validate_and_extract_resume(
            "resume.pdf", "application/pdf", _resume_pdf_with_open_action(action)
        )
    except ValueError as error:
        assert "Unsafe PDF open action" in str(error)
    else:
        raise AssertionError("JavaScript PDF open action was accepted")


def _resume_pdf_with_link(target: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 5, "Portfolio", link=target, new_x="LMARGIN", new_y="NEXT")
    for line in RESUME_TEXT.splitlines():
        pdf.multi_cell(0, 5, line or " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def test_pdf_web_and_email_links_are_accepted() -> None:
    for target in ("https://example.com/alex", "mailto:alex@example.com"):
        result = validate_and_extract_resume(
            "resume.pdf", "application/pdf", _resume_pdf_with_link(target)
        )
        assert result.source_type == "pdf"


def test_plain_pdf_extraction_preserves_hyperlink_urls() -> None:
    text = extract_text_pdf_plain(
        "resume.pdf",
        "application/pdf",
        _resume_pdf_with_link("https://example.com/alex"),
    )
    assert "Links:" in text
    assert "https://example.com/alex" in text


def test_pdf_local_file_link_is_rejected() -> None:
    try:
        validate_and_extract_resume(
            "resume.pdf", "application/pdf", _resume_pdf_with_link("file:///etc/passwd")
        )
    except ValueError as error:
        assert "Unsafe PDF link action" in str(error)
    else:
        raise AssertionError("local file PDF link was accepted")


def test_image_only_pdf_is_rejected() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    content = stream.getvalue()
    try:
        validate_and_extract_resume("scan.pdf", "application/pdf", content)
    except ValueError as error:
        assert "Image-only and scanned PDFs" in str(error)
    else:
        raise AssertionError("image-only PDF was accepted")


def test_parser_extracts_all_required_sections() -> None:
    profile = parse_resume_text(RESUME_TEXT)
    assert profile.basics.full_name == "Alex Chen"
    assert profile.basics.portfolio_url is None
    assert profile.education[0].expected_graduation is True
    assert profile.education[0].graduation_year == 2027
    assert profile.education[0].specialization == "AI"
    assert profile.work_experience[0].company == "Acme Inc."
    assert profile.work_experience[0].description.startswith("Built a FastAPI")
    assert profile.projects[0].name == "Intern Finder"
    assert {skill.name for skill in profile.skills} >= {
        "Python",
        "JavaScript",
        "FastAPI",
        "PostgreSQL",
        "AWS",
    }
    assert set(profile.skills[0].model_dump(exclude_none=True)) == {"name"}
    assert profile.certifications[0].name == "AWS Certified Cloud Practitioner"
    assert profile.languages[1].name == "French"
    assert profile.awards[0].title == "Dean's Honour List 2025"
