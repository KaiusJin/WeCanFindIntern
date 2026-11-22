"""Collect each board, store postings, and record progress independently."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial
from typing import Any

from wecanfindintern.waterlooworks.application.ports import (
    JobReader,
    JobRepository,
    WaterlooWorksBoardUnavailable,
)
from wecanfindintern.waterlooworks.application.state import WaterlooWorksSnapshot
from wecanfindintern.waterlooworks.domain.boards import WATERLOOWORKS_BOARDS


class WaterlooWorksCollector:
    def __init__(
        self,
        *,
        reader: JobReader,
        repository: JobRepository,
        snapshot: WaterlooWorksSnapshot,
    ) -> None:
        self.reader = reader
        self.repository = repository
        self.snapshot = snapshot

    async def collect_all(self, target: dict[str, Any]) -> None:
        run_id: str | None = None
        try:
            run_id = await asyncio.to_thread(
                self.repository.start_run,
                self.snapshot.boards,
            )
            self.snapshot.run_id = run_id
            board_errors: list[str] = []
            skipped_boards: list[str] = []
            known_job_ids = await asyncio.to_thread(self.repository.known_job_ids)
            for index, (board_name, board_url) in enumerate(WATERLOOWORKS_BOARDS, start=1):
                board_state = self._board_state(board_name)
                board_state.update(
                    status="collecting",
                    discovered_count=0,
                    posting_inserted_count=0,
                    posting_known_count=0,
                    posting_failed_count=0,
                    error=None,
                )
                await asyncio.to_thread(
                    self.repository.mark_board_collecting,
                    run_id,
                    board_name,
                )
                self.snapshot.page_url = board_url
                self.snapshot.message = (
                    f"Reading WaterlooWorks board {index}/{len(WATERLOOWORKS_BOARDS)}: "
                    f"{board_name.replace('_', ' ').title()}…"
                )
                try:
                    raw_board_jobs = await self.reader.read_board(
                        target,
                        board_name=board_name,
                        board_url=board_url,
                        known_job_ids=known_job_ids,
                        on_display_mode=partial(
                            self._report_display_mode,
                            board_state=board_state,
                            board_index=index,
                            board_name=board_name,
                        ),
                    )
                    for raw in raw_board_jobs:
                        if isinstance(raw, dict):
                            raw["jobBoard"] = board_name
                            raw["jobBoardUrl"] = board_url
                    outcomes, posting_errors = await asyncio.to_thread(
                        self.repository.store_board_postings,
                        run_id,
                        board_name,
                        raw_board_jobs,
                    )
                    known_job_ids = await asyncio.to_thread(self.repository.known_job_ids)
                    board_state.update(
                        status="completed",
                        discovered_count=len(raw_board_jobs),
                        posting_inserted_count=outcomes["posting_inserted"],
                        posting_known_count=outcomes["posting_known"],
                        posting_failed_count=outcomes["posting_failed"],
                        error="; ".join(posting_errors[:3])[:500] or None,
                    )
                except WaterlooWorksBoardUnavailable as error:
                    reason = str(error).splitlines()[0][:240]
                    board_state.update(status="skipped", error=reason)
                    await asyncio.to_thread(
                        self.repository.mark_board_skipped,
                        run_id,
                        board_name,
                        reason,
                    )
                    skipped_boards.append(board_name)
                except Exception as error:
                    error_text = str(error).splitlines()[0][:240]
                    board_state.update(status="failed", error=error_text)
                    await asyncio.to_thread(
                        self.repository.mark_board_failed,
                        run_id,
                        board_name,
                        error_text,
                    )
                    board_errors.append(f"{board_name}: {error_text}")
                    self.snapshot.board_failed_count = len(board_errors)

            totals = await asyncio.to_thread(
                self.repository.finish_run,
                run_id,
                "; ".join(board_errors)[:1000] or None,
            )
            self.snapshot.unique_job_count = totals["unique_job_count"]
            self.snapshot.posting_inserted_count = totals["posting_inserted_count"]
            self.snapshot.posting_known_count = totals["posting_known_count"]
            self.snapshot.posting_failed_count = totals["posting_failed_count"]
            self.snapshot.board_failed_count = totals["board_failed_count"]
            self.snapshot.finished_at = datetime.now(UTC).isoformat()
            self.snapshot.last_job_update_at = await asyncio.to_thread(
                self.repository.latest_job_update_at
            )
            self.snapshot.status = totals["status"]
            if totals["posting_failed_count"] or totals["board_failed_count"]:
                self.snapshot.message = (
                    f"{totals['posting_failed_count']} posting failures across "
                    f"{totals['board_failed_count']} boards."
                )
            elif skipped_boards:
                suffix = "s" if len(skipped_boards) != 1 else ""
                self.snapshot.message = (
                    f"Skipped {len(skipped_boards)} unavailable WaterlooWorks board{suffix}."
                )
            else:
                self.snapshot.message = ""
        except asyncio.CancelledError:
            if run_id is not None:
                await asyncio.to_thread(self.repository.fail_run, run_id, "Collection cancelled")
            raise
        except Exception as error:
            if run_id is not None:
                await asyncio.to_thread(self.repository.fail_run, run_id, str(error))
            self.snapshot.status = "failed"
            self.snapshot.message = str(error)
            self.snapshot.finished_at = datetime.now(UTC).isoformat()

    def _board_state(self, board_name: str) -> dict[str, Any]:
        return next(board for board in self.snapshot.boards if board["name"] == board_name)

    def _report_display_mode(
        self,
        display_mode: str,
        *,
        board_state: dict[str, Any],
        board_index: int,
        board_name: str,
    ) -> None:
        board_state["display_mode"] = display_mode
        self.snapshot.message = (
            f"Reading the {display_mode} results API for board "
            f"{board_index}/{len(WATERLOOWORKS_BOARDS)}: "
            f"{board_name.replace('_', ' ').title()}…"
        )
