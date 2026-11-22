"""ATS board registry: which company boards to poll, and how to find new ones."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

PROVIDERS = ("greenhouse", "lever", "ashby", "workday")

_URL_PATTERNS = {
    "greenhouse": re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_(?:app|board)\?for=)?"
        r"([A-Za-z0-9_-]+)",
        re.I,
    ),
    "lever": re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I),
}
_WORKDAY_JOBS = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([A-Za-z0-9_-]+)",
    re.I,
)
_WORKDAY_SITE = re.compile(
    r"https?://(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[a-z]{2}/)?recruiting/([A-Za-z0-9_-]+)/"
    r"([A-Za-z0-9_-]+)",
    re.I,
)
_NOT_TOKENS = {"embed", "jobs", "v1", "api", "boards", "job_app", "job_board"}


@dataclass(frozen=True, order=True)
class Board:
    provider: str
    token: str
    company: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.token.casefold()


def board_from_url(url: str | None, company: str | None = None) -> Board | None:
    """Return the ATS board a posting URL belongs to, or None for other hosts."""
    if not url:
        return None
    workday = workday_token(url)
    if workday:
        return Board("workday", workday, company or None)
    for provider, pattern in _URL_PATTERNS.items():
        match = pattern.search(url)
        if match:
            token = unquote(match[1]).strip()
            if token and token.casefold() not in _NOT_TOKENS:
                return Board(provider, token, company or None)
    return None


def workday_token(url: str) -> str | None:
    """Workday boards are ``host/tenant/site``; the CXS API lives on the same host."""
    match = _WORKDAY_JOBS.match(url)
    if match:
        tenant, pod, site = match[1].lower(), match[2].lower(), match[3]
        if site.casefold() in {"wday", "job", "jobs"}:
            return None
        return f"{tenant}.{pod}.myworkdayjobs.com/{tenant}/{site}"
    match = _WORKDAY_SITE.match(url)
    if match:
        return f"{match[1].lower()}.myworkdaysite.com/{match[2].lower()}/{match[3]}"
    return None


_GH_JID = re.compile(r"[?&#]gh_jid=(\d+)", re.I)
_HOST_NOISE = {
    "www",
    "careers",
    "career",
    "jobs",
    "job",
    "apply",
    "boards",
    "join",
    "work",
    "com",
    "co",
    "io",
    "ai",
    "net",
    "org",
    "us",
    "app",
    "en",
    "hire",
    "talent",
}


def greenhouse_embed_candidates(url: str | None) -> tuple[str, list[str]] | None:
    """For a company page embedding Greenhouse (``?gh_jid=``), return the job id and
    plausible board tokens derived from the host (``careers.withwaymo.com`` ->
    ``withwaymo``, ``waymo``). Callers confirm a token against the Greenhouse API."""
    if not url:
        return None
    match = _GH_JID.search(url)
    if not match:
        return None
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/", 1)[0].lower()
    labels = [label for label in re.split(r"[.-]", host) if label and label not in _HOST_NOISE]
    candidates: list[str] = []
    for label in labels:
        for variant in (
            label,
            re.sub(r"^(with|join|life\s*at|team)", "", label),
            re.sub(r"(careers?|jobs|hq|inc|corp)$", "", label),
        ):
            if variant and len(variant) > 1 and variant not in candidates:
                candidates.append(variant)
    if len(labels) > 1:
        candidates.append("".join(labels))
    # Hosted boards such as app.careerpuck.com/job-board/lyft/... carry the company in the path.
    path = re.sub(r"^https?://[^/]+", "", url, flags=re.I).split("?", 1)[0]
    for segment in path.strip("/").split("/"):
        segment = segment.lower()
        if (
            segment
            and not segment.isdigit()
            and segment not in _HOST_NOISE | {"job-board", "search"}
            and segment not in candidates
            and re.fullmatch(r"[a-z0-9_-]{2,40}", segment)
        ):
            candidates.append(segment)
    return match[1], candidates


def merge_boards(*groups: Iterable[Board]) -> list[Board]:
    """Union boards by (provider, token); the first non-empty company name wins."""
    merged: dict[tuple[str, str], Board] = {}
    for group in groups:
        for board in group:
            current = merged.get(board.key)
            if current is None or (not current.company and board.company):
                merged[board.key] = board
    return sorted(merged.values())


def load_boards(path: Path) -> list[Board]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    boards = []
    for row in payload.get("boards", []):
        provider = str(row.get("provider", "")).strip().lower()
        token = str(row.get("token", "")).strip()
        if provider in PROVIDERS and token:
            boards.append(Board(provider, token, (row.get("company") or "").strip() or None))
    return merge_boards(boards)


def save_boards(path: Path, boards: Iterable[Board], *, note: str) -> None:
    rows = [
        {"provider": b.provider, "token": b.token, "company": b.company}
        for b in merge_boards(boards)
    ]
    path.write_text(
        json.dumps({"note": note, "boards": rows}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def boards_from_listings(listings: Iterable[dict]) -> list[Board]:
    """Extract boards from listing rows shaped like ``{"url": ..., "company_name": ...}``."""
    return merge_boards(
        board
        for row in listings
        if (board := board_from_url(row.get("url"), row.get("company_name"))) is not None
    )
