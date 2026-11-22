# Indeed and LinkedIn collectors

This is the local WeCanFindIntern subset of
[JobSpy](https://github.com/cullenwatson/JobSpy), based on version 1.1.82.
The source is organized into independent `indeed/` and `linkedin/` directories.
`shared/` contains the query models, HTTP utilities and dispatch function.

Install from the repository root:

```bash
python -m pip install -r requirements.txt
```

```python
from cloud.sources.jobspy.shared.dispatch import scrape_jobs

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

See [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) for every upstream used by the cloud collector.
