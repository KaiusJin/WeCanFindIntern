from __future__ import annotations

from decimal import Decimal

from wecanfindintern.domain.classification import (
    JobCategory,
    OpportunityType,
    ScheduleType,
    classify_job,
)
from wecanfindintern.domain.location import parse_location
from wecanfindintern.domain.normalization import annualize_salary
from wecanfindintern.domain.salary import extract_salary_from_description


def test_coop_is_separate_from_full_time_schedule() -> None:
    result = classify_job(
        title="Backend Software Developer Co-op",
        description=(
            "Full-time 4 month placement. Build REST APIs with Python, FastAPI, AWS, "
            "PostgreSQL and Docker. Currently pursuing a bachelor's degree. "
            "Requires 2-3 years of experience."
        ),
        employment_types=["full_time", "internship"],
        work_mode="hybrid",
    )

    assert result.opportunity_type is OpportunityType.CO_OP
    assert result.primary_schedule_type is ScheduleType.FULL_TIME
    assert result.job_category is JobCategory.SOFTWARE_ENGINEERING
    assert "backend" in result.job_subcategories
    assert {"python", "fastapi", "aws", "postgresql", "docker"} <= set(result.skill_tags)
    assert {"co_op", "full_time", "hybrid"} <= set(result.display_tags)


def test_internship_from_provider_type_when_title_is_student_role() -> None:
    result = classify_job(
        title="Data Analyst Student",
        description="Work with SQL, Python, Tableau and Power BI.",
        employment_types=["internship", "part_time"],
    )

    assert result.opportunity_type is OpportunityType.INTERNSHIP
    assert result.primary_schedule_type is ScheduleType.PART_TIME
    assert result.job_category is JobCategory.DATA_AI
    assert {"sql", "python", "tableau", "power_bi"} <= set(result.skill_tags)


def test_regular_senior_role_does_not_become_internship() -> None:
    result = classify_job(
        title="Senior Platform Engineer",
        description="Kubernetes, Terraform, AWS and site reliability engineering.",
        employment_types=["full_time"],
    )

    assert result.opportunity_type is OpportunityType.REGULAR
    assert result.job_category is JobCategory.CLOUD_DEVOPS


def test_description_technology_does_not_override_title_category() -> None:
    architect = classify_job(
        title="Architectural Designer",
        description="Use design software and collaborate with software teams.",
    )
    mechanical = classify_job(
        title="Mechanical Engineering Lead",
        description="Use Python for robotics simulations and data analysis.",
    )

    assert architect.job_category is JobCategory.ARCHITECTURE_PLANNING
    assert mechanical.job_category is JobCategory.ENGINEERING


def test_skill_matching_does_not_confuse_java_and_javascript() -> None:
    result = classify_job(
        title="Java Developer",
        description="Develop Spring Boot services.",
        employment_types=["full_time"],
    )

    assert "java" in result.skill_tags
    assert "javascript" not in result.skill_tags


def test_expanded_skill_rules_cover_modern_data_and_frontend_stack() -> None:
    result = classify_job(
        title="Data Platform Engineer",
        description=(
            "Build pipelines with Kafka, Airflow and Snowflake. Maintain a React "
            "dashboard deployed with Kubernetes and GitHub Actions."
        ),
    )

    assert {
        "kafka",
        "airflow",
        "snowflake",
        "react",
        "kubernetes",
        "github_actions",
    } <= set(result.skill_tags)


def test_expanded_skill_rules_cover_ai_agent_stack() -> None:
    result = classify_job(
        title="AI Agent Engineer",
        description=(
            "Build agentic workflows with LangChain and LangGraph, using RAG, "
            "embeddings, vector databases, tool calling and prompt engineering."
        ),
    )

    assert {
        "ai_agents",
        "langchain",
        "langgraph",
        "rag",
        "embeddings",
        "vector_database",
        "tool_calling",
        "prompt_engineering",
    } <= set(result.skill_tags)


def test_expanded_skill_rules_cover_office_software_and_operating_systems() -> None:
    result = classify_job(
        title="IT Support Specialist",
        description=(
            "Support Windows and macOS workstations, Microsoft 365, Outlook, "
            "SharePoint, Google Workspace and Ubuntu environments."
        ),
    )

    assert {
        "windows",
        "macos",
        "microsoft_365",
        "outlook",
        "sharepoint",
        "google_workspace",
        "ubuntu",
    } <= set(result.skill_tags)


def test_dotnet_does_not_imply_csharp_without_language_evidence() -> None:
    result = classify_job(
        title=".NET Platform Engineer",
        description="Maintain ASP.NET services that also host F# workloads.",
    )

    assert "dotnet" in result.skill_tags
    assert "csharp" not in result.skill_tags


def test_salary_annualization() -> None:
    assert annualize_salary(Decimal("25"), "hourly") == Decimal("52000.00")
    assert annualize_salary(Decimal("5000"), "monthly") == Decimal("60000.00")
    assert annualize_salary(Decimal("80000"), "yearly") == Decimal("80000.00")
    assert annualize_salary(Decimal("100"), "unknown") is None


def test_bare_pay_range_preserves_hourly_decimals() -> None:
    result = extract_salary_from_description(
        "Applications are open. Pay Range: $14.5 - $30.51",
        country_code="US",
    )
    assert result is not None
    assert result.interval == "hourly"
    assert result.minimum == Decimal("14.5")
    assert result.maximum == Decimal("30.51")
    assert result.currency == "USD"


def test_salary_parser_skips_postal_code_before_pay_range() -> None:
    result = extract_salary_from_description(
        """
        Job Location Postal Code: 77058-2900
        The US base salary range for this position is
        $20.00 - $45.00
        """,
        country_code="US",
    )
    assert result is not None
    assert result.interval == "hourly"
    assert result.minimum == Decimal("20.00")
    assert result.maximum == Decimal("45.00")


def test_canadian_location_uses_consistent_region_code() -> None:
    full = parse_location("Toronto, Ontario, Canada")
    short = parse_location("Toronto, ON, CA")
    inferred = parse_location("Toronto, Ontario")

    assert (full.region_code, full.country_code) == ("ON", "CA")
    assert (short.region_code, short.country_code) == ("ON", "CA")
    assert (inferred.region_code, inferred.country_code) == ("ON", "CA")
    assert (full.region_name, full.region_type) == ("Ontario", "province")
    assert full.country_name == "Canada"


def test_canadian_city_alias_uses_one_official_display_name() -> None:
    plain = parse_location("Montreal, QC, Canada")
    accented = parse_location("Montréal, Quebec, CA")

    assert plain.city == "Montréal"
    assert accented.city == "Montréal"
    assert plain.normalized == accented.normalized
