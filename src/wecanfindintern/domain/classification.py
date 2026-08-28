"""Deterministic, versioned classification for job display and filtering."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, Field

CLASSIFICATION_VERSION = 3


class OpportunityType(StrEnum):
    INTERNSHIP = "internship"
    CO_OP = "co_op"
    NEW_GRAD = "new_grad"
    APPRENTICESHIP = "apprenticeship"
    REGULAR = "regular"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    SEASONAL = "seasonal"
    UNKNOWN = "unknown"


class ScheduleType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    FLEXIBLE = "flexible"
    UNKNOWN = "unknown"


class JobCategory(StrEnum):
    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_AI = "data_ai"
    CYBERSECURITY = "cybersecurity"
    CLOUD_DEVOPS = "cloud_devops"
    QA_TESTING = "qa_testing"
    PRODUCT_DESIGN = "product_design"
    PRODUCT_MANAGEMENT = "product_management"
    IT_SUPPORT = "it_support"
    HARDWARE_EMBEDDED = "hardware_embedded"
    RESEARCH = "research"
    BUSINESS_OPERATIONS = "business_operations"
    FINANCE = "finance"
    MARKETING_SALES = "marketing_sales"
    HUMAN_RESOURCES = "human_resources"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    SKILLED_TRADES = "skilled_trades"
    ENGINEERING = "engineering"
    ARCHITECTURE_PLANNING = "architecture_planning"
    LEGAL = "legal"
    CUSTOMER_SERVICE = "customer_service"
    SUPPLY_CHAIN = "supply_chain"
    ADMINISTRATIVE = "administrative"
    OTHER = "other"


class JobClassification(BaseModel):
    opportunity_type: OpportunityType
    schedule_types: list[ScheduleType] = Field(default_factory=list)
    primary_schedule_type: ScheduleType
    job_category: JobCategory
    job_subcategories: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    requirement_tags: list[str] = Field(default_factory=list)
    display_tags: list[str] = Field(default_factory=list)
    classification_version: int = CLASSIFICATION_VERSION


CATEGORY_RULES: tuple[tuple[JobCategory, tuple[str, ...]], ...] = (
    (
        JobCategory.CYBERSECURITY,
        (
            "cybersecurity",
            "cyber security",
            "security engineer",
            "information security",
            "soc analyst",
            "penetration test",
            "threat intelligence",
        ),
    ),
    (
        JobCategory.DATA_AI,
        (
            "machine learning",
            "ml engineer",
            "artificial intelligence",
            "ai engineer",
            "ai researcher",
            "data intern",
            "analytics intern",
            "data scientist",
            "data engineer",
            "data analyst",
            "analytics engineer",
            "business intelligence",
        ),
    ),
    (
        JobCategory.CLOUD_DEVOPS,
        (
            "devops",
            "site reliability",
            "sre",
            "cloud engineer",
            "platform engineer",
            "infrastructure engineer",
            "release engineer",
        ),
    ),
    (
        JobCategory.QA_TESTING,
        (
            "quality assurance",
            "qa engineer",
            "test engineer",
            "software tester",
            "automation tester",
            "sdet",
        ),
    ),
    (
        JobCategory.HARDWARE_EMBEDDED,
        (
            "embedded",
            "firmware",
            "hardware engineer",
            "electrical engineer",
            "fpga",
            "robotics engineer",
            "systems engineering intern",
        ),
    ),
    (
        JobCategory.SOFTWARE_ENGINEERING,
        (
            "software",
            "developer",
            "frontend",
            "front end",
            "backend",
            "back end",
            "full stack",
            "fullstack",
            "mobile engineer",
            "ios developer",
            "android developer",
            "web engineer",
        ),
    ),
    (
        JobCategory.PRODUCT_DESIGN,
        ("product designer", "ux designer", "ui designer", "user experience"),
    ),
    (
        JobCategory.PRODUCT_MANAGEMENT,
        (
            "product manager",
            "product management",
            "product owner",
            "technical program manager",
            "technical project manager",
        ),
    ),
    (
        JobCategory.IT_SUPPORT,
        (
            "it support",
            "technical support",
            "help desk",
            "service desk",
            "systems administrator",
            "network administrator",
        ),
    ),
    (
        JobCategory.RESEARCH,
        ("research assistant", "research scientist", "research intern", "researcher"),
    ),
    (
        JobCategory.FINANCE,
        (
            "financial analyst",
            "accounting",
            "accountant",
            "audit",
            "investment",
            "finance",
            "portfolio engineering",
        ),
    ),
    (
        JobCategory.MARKETING_SALES,
        ("marketing", "sales", "business development", "account executive"),
    ),
    (
        JobCategory.HUMAN_RESOURCES,
        ("human resources", "hr coordinator", "recruiter", "talent acquisition"),
    ),
    (
        JobCategory.HEALTHCARE,
        ("nurse", "clinical", "pharmac", "medical", "healthcare"),
    ),
    (
        JobCategory.EDUCATION,
        ("teacher", "instructor", "teaching assistant", "educator", "tutor"),
    ),
    (
        JobCategory.SKILLED_TRADES,
        ("electrician", "mechanic", "technician", "welder", "plumber", "carpenter"),
    ),
    (
        JobCategory.ARCHITECTURE_PLANNING,
        (
            "architectural designer",
            "architectural technologist",
            "architect intern",
            "urban planner",
            "environmental planner",
            "landscape architect",
        ),
    ),
    (
        JobCategory.ENGINEERING,
        (
            "mechanical engineer",
            "mechanical engineering",
            "civil engineer",
            "civil engineering",
            "chemical engineer",
            "chemical engineering",
            "environmental engineer",
            "field engineer",
            "field engineering",
            "manufacturing engineer",
            "industrial engineer",
            "process engineer",
            "engineering intern",
        ),
    ),
    (
        JobCategory.LEGAL,
        ("lawyer", "legal counsel", "paralegal", "legal assistant", "compliance counsel"),
    ),
    (
        JobCategory.CUSTOMER_SERVICE,
        ("customer service", "customer success", "client service", "support representative"),
    ),
    (
        JobCategory.SUPPLY_CHAIN,
        ("supply chain", "logistics", "procurement", "buyer", "inventory planner"),
    ),
    (
        JobCategory.ADMINISTRATIVE,
        ("administrative assistant", "office administrator", "executive assistant"),
    ),
    (
        JobCategory.BUSINESS_OPERATIONS,
        ("business analyst", "operations", "project coordinator", "program coordinator"),
    ),
)

SUBCATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "frontend": ("frontend", "front end", "react", "angular", "vue"),
    "backend": ("backend", "back end", "api development", "server side"),
    "full_stack": ("full stack", "fullstack"),
    "mobile": ("mobile", "ios", "android", "react native", "flutter"),
    "game_development": ("game developer", "gameplay", "unity", "unreal engine"),
    "data_engineering": ("data engineer", "etl", "data pipeline", "spark"),
    "data_science": ("data scientist", "statistical modeling", "data science"),
    "machine_learning": ("machine learning", "deep learning", "ml engineer"),
    "generative_ai": ("generative ai", "large language model", "llm", "rag"),
    "business_intelligence": ("business intelligence", "power bi", "tableau"),
    "devops": ("devops", "ci cd", "continuous integration"),
    "site_reliability": ("site reliability", "sre"),
    "cloud": ("cloud engineer", "aws", "azure", "gcp"),
    "application_security": ("application security", "appsec"),
    "security_operations": ("soc analyst", "security operations", "incident response"),
    "embedded": ("embedded", "microcontroller", "rtos"),
    "firmware": ("firmware",),
    "robotics": ("robotics", "ros"),
    "automation_testing": ("test automation", "automation tester", "selenium", "cypress"),
    "ux_ui": ("ux", "ui design", "user experience", "figma"),
}

SKILL_RULES: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java",),
    "javascript": ("javascript", "js"),
    "typescript": ("typescript",),
    "c": ("c language",),
    "cpp": ("c++", "cpp"),
    "csharp": ("c#", "c sharp", ".net"),
    "go": ("golang", "go language"),
    "rust": ("rust",),
    "sql": ("sql",),
    "r": ("r programming", "r language"),
    "swift": ("swift",),
    "kotlin": ("kotlin",),
    "react": ("react", "reactjs", "react.js"),
    "angular": ("angular",),
    "vue": ("vue", "vuejs", "vue.js"),
    "nodejs": ("node.js", "nodejs"),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi",),
    "spring": ("spring boot", "spring framework"),
    "dotnet": (".net", "asp.net"),
    "aws": ("aws", "amazon web services"),
    "azure": ("azure",),
    "gcp": ("gcp", "google cloud"),
    "docker": ("docker", "containerization"),
    "kubernetes": ("kubernetes", "k8s"),
    "terraform": ("terraform",),
    "linux": ("linux",),
    "git": ("git", "github", "gitlab"),
    "postgresql": ("postgresql", "postgres"),
    "mysql": ("mysql",),
    "mongodb": ("mongodb",),
    "redis": ("redis",),
    "spark": ("apache spark", "pyspark"),
    "pandas": ("pandas",),
    "pytorch": ("pytorch",),
    "tensorflow": ("tensorflow",),
    "scikit_learn": ("scikit-learn", "sklearn"),
    "llm": ("large language model", "llm"),
    "rest_api": ("rest api", "restful"),
    "graphql": ("graphql",),
    "microservices": ("microservices", "microservice architecture"),
    "agile": ("agile", "scrum"),
    "jira": ("jira",),
    "figma": ("figma",),
    "power_bi": ("power bi",),
    "tableau": ("tableau",),
}

REQUIREMENT_RULES: dict[str, tuple[str, ...]] = {
    "visa_sponsorship_available": ("visa sponsorship available", "will sponsor"),
    "no_visa_sponsorship": ("no sponsorship", "unable to sponsor", "will not sponsor"),
    "security_clearance": ("security clearance", "secret clearance", "top secret"),
    "driver_license": ("driver s license", "drivers license", "valid driving licence"),
    "travel_required": ("travel required", "willingness to travel", "able to travel"),
    "relocation_available": ("relocation assistance", "relocation package"),
    "weekend_shift": ("weekend shift", "weekends required"),
    "evening_shift": ("evening shift", "night shift", "overnight shift"),
}


def classify_job(
    *,
    title: str,
    description: str | None,
    employment_types: Iterable[str] = (),
    source_skills: Iterable[str] = (),
    work_mode: str = "unknown",
) -> JobClassification:
    title_text = normalize_for_matching(title)
    description_text = normalize_for_matching(description)
    combined = f"{title_text} {description_text}".strip()
    employment = {normalize_for_matching(value).replace(" ", "_") for value in employment_types}

    opportunity = classify_opportunity(title_text, combined, employment)
    schedules = classify_schedules(title_text, combined, employment)
    category = classify_category(title_text, combined)
    # Role specialization is title-derived. Technologies merely mentioned in a JD
    # belong in skill_tags and must not silently redefine the role itself.
    subcategories = matching_labels(title_text, SUBCATEGORY_RULES)
    skills = unique_preserving_order(
        [normalize_tag(skill) for skill in source_skills if normalize_tag(skill)]
        + matching_labels(combined, SKILL_RULES)
    )
    requirements = matching_labels(combined, REQUIREMENT_RULES)
    display_tags = build_display_tags(
        opportunity=opportunity,
        schedules=schedules,
        category=category,
        work_mode=work_mode,
        skills=skills,
    )
    return JobClassification(
        opportunity_type=opportunity,
        schedule_types=schedules,
        primary_schedule_type=schedules[0] if schedules else ScheduleType.UNKNOWN,
        job_category=category,
        job_subcategories=subcategories,
        skill_tags=skills[:30],
        requirement_tags=requirements,
        display_tags=display_tags,
    )


def classify_opportunity(
    title: str,
    combined: str,
    employment: set[str],
) -> OpportunityType:
    if "co_op" in employment or contains_any(title, ("co op", "coop")):
        return OpportunityType.CO_OP
    if contains_any(title, ("intern", "internship", "summer student", "student placement")):
        return OpportunityType.INTERNSHIP
    if "new_grad" in employment or contains_any(
        title,
        ("new grad", "new graduate", "graduate program", "early career"),
    ):
        return OpportunityType.NEW_GRAD
    if contains_any(combined, ("apprentice", "apprenticeship")):
        return OpportunityType.APPRENTICESHIP
    if "internship" in employment:
        return OpportunityType.INTERNSHIP
    if "contract" in employment or contains_any(title, ("contract", "contractor")):
        return OpportunityType.CONTRACT
    if "temporary" in employment or contains_any(title, ("temporary", "temp position")):
        return OpportunityType.TEMPORARY
    if contains_any(title, ("seasonal",)):
        return OpportunityType.SEASONAL
    if title:
        return OpportunityType.REGULAR
    return OpportunityType.UNKNOWN


def classify_schedules(title: str, combined: str, employment: set[str]) -> list[ScheduleType]:
    schedules: list[ScheduleType] = []
    if "full_time" in employment or contains_any(title, ("full time", "fulltime")):
        schedules.append(ScheduleType.FULL_TIME)
    if "part_time" in employment or contains_any(title, ("part time", "parttime")):
        schedules.append(ScheduleType.PART_TIME)
    if not schedules and contains_any(combined, ("full time position", "full time hours")):
        schedules.append(ScheduleType.FULL_TIME)
    if not schedules and contains_any(combined, ("part time position", "part time hours")):
        schedules.append(ScheduleType.PART_TIME)
    if len(schedules) > 1:
        return [ScheduleType.FLEXIBLE, *schedules]
    return schedules or [ScheduleType.UNKNOWN]


def classify_category(title: str, combined: str) -> JobCategory:
    for category, patterns in CATEGORY_RULES:
        if contains_any(title, patterns):
            return category
    return JobCategory.OTHER


def build_display_tags(
    *,
    opportunity: OpportunityType,
    schedules: list[ScheduleType],
    category: JobCategory,
    work_mode: str,
    skills: list[str],
) -> list[str]:
    values = [opportunity.value, category.value]
    values.extend(item.value for item in schedules if item is not ScheduleType.UNKNOWN)
    if work_mode != "unknown":
        values.append(work_mode)
    values.extend(skills[:5])
    return unique_preserving_order(values)


def matching_labels(text: str, rules: dict[str, tuple[str, ...]]) -> list[str]:
    return [label for label, patterns in rules.items() if contains_any(text, patterns)]


def contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(contains_phrase(text, pattern) for pattern in patterns)


def contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_for_matching(phrase)
    if not normalized_phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", text) is not None


def normalize_for_matching(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w+#.]+", " ", text)
    return " ".join(text.split())


def normalize_tag(value: str) -> str:
    return normalize_for_matching(value).replace(" ", "_")[:80]


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
