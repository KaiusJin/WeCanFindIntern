"""Prompts for DeepSeek recruiting season extraction fallback."""

from __future__ import annotations


def build_recruiting_term_system_prompt(schema_json: str) -> str:
    return f"""
You extract the job's work term (the season/year when the internship or co-op starts
or is performed) from a job title and selected description lines. Return one JSON
object that conforms exactly to this schema:
{schema_json}

Rules:
- Normalize Autumn to fall.
- Recognize full names, common abbreviations, two-digit years, apostrophe years,
  academic term codes, and words such as term, semester, intake, internship, or program.
- Accept either order and natural phrases, including date ranges that clearly identify
  one North American recruiting season.
- A January/February start maps to winter, March/April to spring, May-August to summer,
  and September-November to fall. Use the start month when a dated work period is explicit.
- Ignore application deadlines, recruiting dates, and posting dates.
- Never infer a season from geography, market convention, or internship type.
- Return found=true only when one defensible season and four-digit year are explicitly stated.
- If multiple conflicting terms are present and no single role term is clear, return found=false.
- Evidence must be an exact short substring copied from the supplied context.
- If no defensible term exists, set found=false, season/year/evidence=null, confidence=1.
""".strip()
