from __future__ import annotations

from decimal import Decimal

from wecanfindintern.domain.classification import (
    JobCategory,
    OpportunityType,
    ScheduleType,
    classify_job,
)
from wecanfindintern.domain.jobs import annualize_salary, parse_location


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


def test_salary_annualization() -> None:
    assert annualize_salary(Decimal("25"), "hourly") == Decimal("52000.00")
    assert annualize_salary(Decimal("5000"), "monthly") == Decimal("60000.00")
    assert annualize_salary(Decimal("80000"), "yearly") == Decimal("80000.00")
    assert annualize_salary(Decimal("100"), "unknown") is None


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
