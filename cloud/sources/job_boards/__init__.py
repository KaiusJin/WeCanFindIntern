"""Public ATS job-board sources (Greenhouse, Lever, Ashby).

These boards publish unauthenticated JSON for every open posting of one company,
so one request per board replaces keyword searches and has no meaningful rate
limit. The board registry says which companies to poll; ``relevance`` keeps only
US/Canada computer-science internships (optionally new-grad roles).
"""

from cloud.sources.job_boards.boards import Board, board_from_url, load_boards
from cloud.sources.job_boards.collector import AtsCollection, collect_ats_jobs

__all__ = ["AtsCollection", "Board", "board_from_url", "collect_ats_jobs", "load_boards"]
