# Third-party notices for the cloud collector

The cloud collector includes or uses the following third-party work.

| Upstream | How it is used | License | Notice |
| --- | --- | --- | --- |
| [JobSpy](https://github.com/cullenwatson/JobSpy) v1.1.82 by Cullen Watson | Source code copied and modified in `cloud/sources/jobspy/indeed/`, `cloud/sources/jobspy/linkedin/` and `cloud/sources/jobspy/shared/` | MIT | [`sources/jobspy/LICENSE`](sources/jobspy/LICENSE) |
| [Stapply ats-scrapers](https://github.com/kalil0321/ats-scrapers) by Kalil Bouzigues | Daily Parquet job dataset read at run time by `cloud/sources/job_boards/stapply.py`; some board identifiers in `cloud/config/job_boards.json` were discovered from it | MIT | [`sources/job_boards/LICENSE.stapply`](sources/job_boards/LICENSE.stapply) |
| [SimplifyJobs Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships) and [New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) | `listings.json` downloaded at run time by `cloud/sources/job_boards/simplify.py`; some board identifiers in `cloud/config/job_boards.json` were discovered from it. The listings themselves are not stored in this repository. | No license published upstream | — |

The Greenhouse, Lever, Ashby and Workday clients in `cloud/sources/job_boards/providers.py`
are original code that calls those vendors' public job-board APIs.
