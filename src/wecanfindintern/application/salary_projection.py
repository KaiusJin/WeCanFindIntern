"""Source-neutral salary projection for API-facing job records."""

from __future__ import annotations

from decimal import Decimal

from wecanfindintern.application.job_models import SalaryResponse
from wecanfindintern.domain.normalization import annualize_salary, to_decimal


def salary_response(
    *,
    interval: str | None,
    minimum: Decimal | str | int | float | None,
    maximum: Decimal | str | int | float | None,
    currency: str | None,
    source: str | None,
    annualized_minimum: Decimal | None = None,
    annualized_maximum: Decimal | None = None,
) -> SalaryResponse | None:
    minimum_value = to_decimal(minimum)
    maximum_value = to_decimal(maximum)
    if minimum_value is None and maximum_value is None:
        return None
    return SalaryResponse(
        interval=interval,
        minimum=minimum_value,
        maximum=maximum_value,
        currency=currency,
        source=source,
        annualized_minimum=(
            annualized_minimum
            if annualized_minimum is not None
            else annualize_salary(minimum_value, interval)
        ),
        annualized_maximum=(
            annualized_maximum
            if annualized_maximum is not None
            else annualize_salary(maximum_value, interval)
        ),
    )
