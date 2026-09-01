# ATS-Style Resume Diagnostics

The UI exposes two independent sections—Resume ATS Score and ATS Match—backed
by separate deterministic API routes. It does not claim to reproduce Workday,
Greenhouse, Taleo, or an employer's
ranking model, and neither score is an admission probability.

## Design references

The implementation follows the transparent scoring patterns used by:

- [CVForge](https://github.com/alteixeira20/CVForge-Fork/blob/main/docs/ATS_SCORING.md),
  which separates parsing (40), structure (20), readability (10), and optional
  job-description keywords (30).
- [ATS Resume Checker](https://github.com/Jahangirhussen/ats-resume-checker),
  which calculates category scores first, then combines keyword coverage (22%),
  structure, formatting, writing, and achievements (14% each), experience
  (10%), and education and contact information (6% each).
- [acenji/ats](https://github.com/acenji/ats), which keeps exact matches, soft
  matches, and missing terms visible as separate evidence.

We deliberately split parsing readiness from job fit so a clean PDF cannot
inflate candidate-job relevance, and a relevant resume cannot hide parsing
failures.

These repositories are design references, not evidence of how a commercial
ATS ranks applicants. Their useful common pattern is auditable category
scoring—not the claim that any one set of weights is universal.

## Resume Parsing Readiness

The Resume ATS Score section calls `POST /api/v1/ats/score` for pasted text.
PDF uploads use the shared `POST /api/v1/resumes/extract-pdf` boundary so the
score can include page-layout evidence.

After the deterministic score is available, the section calls
`POST /api/v1/ats/score/commentary` with that diagnostic and the resume text.
The selected AI provider returns a short assessment, supported strengths, and
prioritized improvements. This qualitative layer cannot alter or replace the
numeric score. If AI generation is unavailable, the score remains visible and
the section shows an inline feedback message.

`score_parsing_readiness` is a pure function. The PDF endpoint provides page
text so page-level extraction and reading-order heuristics can be assessed.
Pasted text runs in `text_only` mode and marks PDF reading order unavailable.

| Category | Maximum |
| --- | ---: |
| Text extraction | 30 |
| Standard section recognition | 25 |
| Contact parsing | 10 |
| Entry structure | 15 |
| PDF reading order | 10 |
| Date consistency | 10 |

Unavailable categories are excluded from the denominator rather than silently
awarding or deducting points. Every category includes evidence and a status.

## Resume-Job Match

The ATS Match section calls `POST /api/v1/ats/match` with resume text and one
target job description.

`score_job_match` extracts source-backed signals with deterministic rules. A
skill is only scored when an alias from the versioned skill taxonomy appears
in the job description, and every match or gap retains the source line.

| Category | Maximum when applicable |
| --- | ---: |
| Required skills | 35 |
| Preferred skills | 15 |
| Explicit experience duration | 20 |
| Explicit education requirement | 10 |
| Role-family alignment | 10 |
| Normalized term relevance | 10 |

Requirements not stated in the job description are `unavailable` and excluded
from the denominator. Eligibility language such as sponsorship, citizenship,
clearance, and driver's-license requirements is surfaced separately and does
not disappear into the total score.

If the description contains no explicit skill, education, or experience
requirement, the module returns **Insufficient evidence** instead of deriving a
precise-looking number from a title or generic wording alone.

The selected AI provider, model, and API key do not affect either score. LLM
output is never accepted as a score.

## Invariants

- The same inputs produce the same score.
- Adding genuine matching evidence cannot reduce a skill-coverage component.
- An unstated education or years requirement cannot create a penalty.
- Text-only input cannot claim PDF-layout analysis.
- Every scored signal has source evidence.
- Prompt-like text inside a resume or job description cannot override scoring.
