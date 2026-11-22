"""Four-month archive rule (SQL text; the database behaviour is covered by db tests)."""

from cloud.storage import maintenance


class RecordingDb:
    def __init__(self, archived=3):
        self.statements: list[str] = []
        self.archived = archived

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.statements.append(" ".join(sql.split()))
        return self

    @property
    def rowcount(self):
        return self.archived


def test_maintain_archives_by_first_write_age_only():
    db = RecordingDb(archived=3)
    assert maintenance.maintain(db) == 3
    joined = "\n".join(db.statements)
    assert "CREATE TABLE IF NOT EXISTS jobs_archive" in joined
    assert "WHERE j.created_at < now() - interval '4 months'" in joined
    assert "DELETE FROM jobs WHERE id IN (SELECT id FROM moved)" in joined
    # The old rules are gone: no unseen-for-14-days expiry and no closed-job purge.
    assert "14 days" not in joined
    assert "status IN (3,4)" not in joined and "SET status=4" not in joined
