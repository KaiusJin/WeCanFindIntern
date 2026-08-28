"""Deterministic English resume-to-profile parser."""

from __future__ import annotations

import re
from collections import defaultdict

from wecanfindintern.profile.models import (
    AwardEntry,
    CertificationEntry,
    EducationEntry,
    LanguageEntry,
    ProfileBasics,
    ProfilePayload,
    ProjectEntry,
    SkillEntry,
    WorkEntry,
)

PARSER_VERSION = "profile-rules.v2"

SECTION_ALIASES = {
    "summary": {"summary", "profile", "professional summary", "objective"},
    "education": {"education", "academic background", "education and training"},
    "work": {"experience", "work experience", "professional experience", "employment"},
    "projects": {"projects", "project experience", "selected projects", "personal projects"},
    "skills": {"skills", "technical skills", "core competencies", "technologies"},
    "certifications": {"certifications", "certificates", "licenses and certifications"},
    "languages": {"languages", "language skills"},
    "awards": {"awards", "honors", "honors and awards", "achievements"},
}

SKILL_ALIASES = {
    "js": "JavaScript",
    "ts": "TypeScript",
    "postgres": "PostgreSQL",
    "amazon web services": "AWS",
    "google cloud platform": "GCP",
    "cpp": "C++",
    "nodejs": "Node.js",
    "react.js": "React",
    "reactjs": "React",
}

SKILL_CATEGORIES = {
    "Programming Languages": {
        "Python",
        "Java",
        "JavaScript",
        "TypeScript",
        "C",
        "C++",
        "C#",
        "Go",
        "Rust",
        "Ruby",
        "Kotlin",
        "Swift",
        "R",
        "SQL",
        "Bash",
        "MATLAB",
    },
    "Frameworks & Libraries": {
        "React",
        "Angular",
        "Vue",
        "Node.js",
        "Express",
        "Django",
        "Flask",
        "FastAPI",
        "Spring",
        "PyTorch",
        "TensorFlow",
        "Pandas",
        "NumPy",
        "Scikit-learn",
    },
    "Databases": {"PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "DynamoDB", "Snowflake"},
    "Cloud & DevOps": {
        "AWS",
        "Azure",
        "GCP",
        "Docker",
        "Kubernetes",
        "Terraform",
        "Jenkins",
        "GitHub Actions",
        "CI/CD",
    },
    "Tools": {"Git", "GitHub", "Linux", "Unix", "Jira", "Figma", "Postman", "Tableau", "Power BI"},
}

DATE_RE = re.compile(
    r"(?P<start>(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)?\s*"
    r"(?:19|20)\d{2})\s*(?:-|–|—|to)\s*(?P<end>Present|Current|(?:Jan(?:uary)?|"
    r"Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)?\s*(?:19|20)\d{2})",
    re.IGNORECASE,
)


def _clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r", "\n").splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" \t|•·")
        if line:
            lines.append(line)
    return lines


def _section_name(line: str) -> str | None:
    candidate = re.sub(r"[^a-z& ]", "", line.lower()).strip()
    if len(candidate) > 40:
        return None
    return next((name for name, aliases in SECTION_ALIASES.items() if candidate in aliases), None)


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header: list[str] = []
    sections: dict[str, list[str]] = defaultdict(list)
    current = None
    for line in lines:
        detected = _section_name(line)
        if detected:
            current = detected
        elif current:
            sections[current].append(line)
        else:
            header.append(line)
    return header, sections


def _extract_basics(header: list[str], sections: dict[str, list[str]]) -> ProfileBasics:
    head = "\n".join(header[:20])
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", head)
    phone_match = re.search(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)", head)
    urls = re.findall(
        r"(?:https?://)?(?:www\.)?[\w.-]+\.(?:com|ca|io|dev|me|net)"
        r"(?:/[\w./?=&%+-]*)?",
        head,
        re.IGNORECASE,
    )
    email_domain = email_match.group(0).split("@", 1)[1].lower() if email_match else None
    urls = [url for url in urls if url.lower() != email_domain]
    name = ""
    for line in header[:6]:
        words = line.split()
        if (
            "@" not in line
            and not re.search(r"https?://|www\.|\d{3}", line, re.I)
            and 1 < len(words) <= 5
            and all(re.search(r"[A-Za-z]", word) for word in words)
        ):
            name = line
            break
    linkedin = next((url for url in urls if "linkedin." in url.lower()), None)
    github = next((url for url in urls if "github." in url.lower()), None)
    portfolio = next((url for url in urls if url not in {linkedin, github}), None)
    return ProfileBasics(
        full_name=name,
        email=email_match.group(0) if email_match else None,
        phone=phone_match.group(0) if phone_match else None,
        linkedin_url=linkedin,
        github_url=github,
        portfolio_url=portfolio,
    )


def _blocks(lines: list[str]) -> list[list[str]]:
    if not lines:
        return []
    blocks, current = [], []
    for line in lines:
        if DATE_RE.search(line) and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _date_range(text: str) -> tuple[str | None, str | None, bool]:
    match = DATE_RE.search(text)
    if not match:
        return None, None, False
    start, end = match.group("start").strip(), match.group("end").strip()
    return start, end, end.lower() in {"present", "current"}


def _extract_education(lines: list[str]) -> list[EducationEntry]:
    entries = []
    degree_re = re.compile(
        r"\b(Bachelor|Master|Doctor|Ph\.?D|B\.?Sc|B\.?A|M\.?Sc|M\.?A|Diploma|Associate)"
        r"[^,|]*",
        re.I,
    )
    for block in _blocks(lines):
        evidence = "\n".join(block)
        degree_match = degree_re.search(evidence)
        years = re.findall(r"(?:19|20)\d{2}", evidence)
        institution = next(
            (
                line
                for line in block
                if re.search(r"University|College|Institute|School", line, re.I)
            ),
            block[0] if block else "",
        )
        major_match = re.search(
            r"(?:in|of)\s+([A-Z][A-Za-z &/-]{2,80})",
            degree_match.group(0) if degree_match else "",
        )
        specialization_match = re.search(
            r"(?:speciali[sz](?:ation|ing)|concentration|option)\s+(?:in\s+)?"
            r"([^|,;\n]{2,100})",
            evidence,
            re.I,
        )
        expected = bool(re.search(r"expected|anticipated", evidence, re.I))
        entries.append(
            EducationEntry(
                institution=institution,
                degree=degree_match.group(0).strip() if degree_match else None,
                major=major_match.group(1).strip() if major_match else None,
                specialization=(
                    specialization_match.group(1).strip()
                    if specialization_match
                    else None
                ),
                graduation_date_text=(("Expected " if expected else "") + years[-1])
                if years
                else None,
                graduation_year=int(years[-1]) if years else None,
                expected_graduation=expected,
                status="studying" if expected else ("graduated" if years else "unknown"),
                evidence=evidence,
                confidence=0.85 if degree_match and institution else 0.6,
            )
        )
    return entries


def _extract_work(lines: list[str]) -> list[WorkEntry]:
    entries = []
    for block in _blocks(lines):
        evidence = "\n".join(block)
        start, end, current = _date_range(evidence)
        first = block[0] if block else ""
        parts = [
            part.strip()
            for part in re.split(r"\s+[|–—]\s+|\s+-\s+", first, maxsplit=2)
            if part.strip()
        ]
        entries.append(
            WorkEntry(
                company=parts[1] if len(parts) > 1 else first,
                title=parts[0] if len(parts) > 1 else None,
                start_date_text=start,
                end_date_text=end,
                is_current=current,
                description="\n".join(block[1:]) or None,
                evidence=evidence,
                confidence=0.75 if start else 0.55,
            )
        )
    return entries


def _extract_projects(lines: list[str]) -> list[ProjectEntry]:
    entries = []
    for block in _blocks(lines):
        evidence = "\n".join(block)
        start, end, _ = _date_range(evidence)
        first = block[0] if block else ""
        url = re.search(r"(?:https?://|www\.)\S+", evidence)
        entries.append(
            ProjectEntry(
                name=re.split(r"\s+[|–—]\s+|\s+-\s+", first, maxsplit=1)[0],
                description="\n".join(block[1:]) or None,
                start_date_text=start,
                end_date_text=end,
                project_url=url.group(0) if url else None,
                github_url=url.group(0) if url and "github" in url.group(0).lower() else None,
                evidence=evidence,
                confidence=0.7,
            )
        )
    return entries


def _extract_skills(lines: list[str], full_text: str) -> list[SkillEntry]:
    candidates = []
    for line in lines:
        value = line.split(":", 1)[1] if ":" in line else line
        for token in re.split(r"[,;|/]|\s{2,}", value):
            token = token.strip(" .()")
            if 1 < len(token) <= 60 and not re.search(
                r"\b(?:and|skills|technologies)\b", token, re.I
            ):
                candidates.append((token, line))
    known = {name for names in SKILL_CATEGORIES.values() for name in names}
    for name in known:
        if re.search(rf"(?<![\w+#.]){re.escape(name)}(?![\w+#.])", full_text, re.I):
            candidates.append((name, name))
    result, seen = [], set()
    for raw, _evidence in candidates:
        normalized = SKILL_ALIASES.get(raw.lower(), raw)
        if normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        result.append(SkillEntry(name=normalized))
    return result


def _simple_entries(lines: list[str]) -> list[str]:
    values = []
    for line in lines:
        values.extend(part.strip(" -•") for part in re.split(r"[;|]", line) if part.strip(" -•"))
    return values


def parse_resume_text(text: str) -> ProfilePayload:
    lines = _clean_lines(text)
    header, sections = _split_sections(lines)
    certifications = [
        CertificationEntry(name=item, evidence=item, confidence=0.7)
        for item in _simple_entries(sections.get("certifications", []))
    ]
    languages = []
    for item in _simple_entries(sections.get("languages", [])):
        parts = [part.strip() for part in re.split(r"[:—–-]", item, maxsplit=1)]
        languages.append(
            LanguageEntry(
                name=parts[0],
                proficiency=parts[1] if len(parts) > 1 else None,
                evidence=item,
                confidence=0.8,
            )
        )
    awards = [
        AwardEntry(title=item, evidence=item, confidence=0.7)
        for item in _simple_entries(sections.get("awards", []))
    ]
    return ProfilePayload(
        basics=_extract_basics(header, sections),
        education=_extract_education(sections.get("education", [])),
        work_experience=_extract_work(sections.get("work", [])),
        projects=_extract_projects(sections.get("projects", [])),
        skills=_extract_skills(sections.get("skills", []), text),
        certifications=certifications,
        languages=languages,
        awards=awards,
    )
