# JobSpy — Indeed and LinkedIn

This is the local WeCanFindIntern subset of
[JobSpy](https://github.com/cullenwatson/JobSpy), based on version 1.1.82.
Only the Indeed and LinkedIn collectors are included.

Install from the repository root:

```bash
python -m pip install ./vendor/JobSpy
```

```python
from jobspy import scrape_jobs

jobs = scrape_jobs(
    site_name=["indeed", "linkedin"],
    search_term="software engineer intern",
    location="Canada",
    country_indeed="Canada",
    results_wanted=40,
)
```

Shared code provides query models, result normalization, Requests sessions,
proxy rotation, retries, HTML conversion, and salary parsing. No other provider
registration or provider-specific query parameters are included.

Upstream authorship and MIT license terms are preserved in [LICENSE](LICENSE).
