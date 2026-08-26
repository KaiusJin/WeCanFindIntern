"""Job collection adapters."""

from .jobspy_adapter import JobSpyQuery, ScrapeResult, scrape_and_normalize

__all__ = ["JobSpyQuery", "ScrapeResult", "scrape_and_normalize"]
