"""
jobspy.jobboard.exceptions
~~~~~~~~~~~~~~~~~~~

This module contains the set of Scrapers' exceptions.
"""


class LinkedInException(Exception):
    def __init__(self, message=None):
        super().__init__(message or "An error occurred with LinkedIn")


class IndeedException(Exception):
    def __init__(self, message=None):
        super().__init__(message or "An error occurred with Indeed")


class InvalidPageCursor(IndeedException):
    """Indeed explicitly rejected the continuation for this search."""


class PaginationLimit(Exception):
    """The source's paging boundary was reached before a confirmed end."""


class LinkedInJobGone(LinkedInException):
    """The detail endpoint confirmed that the listing no longer exists."""


class LinkedInRateLimitException(LinkedInException):
    """The shared LinkedIn request budget exhausted its rate-limit retries."""

    def __init__(self, message, *, retry_after=0.0):
        super().__init__(message)
        self.retry_after = retry_after
