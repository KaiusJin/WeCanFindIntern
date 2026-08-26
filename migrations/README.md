# 数据库迁移

目标数据库为 PostgreSQL 16 或更高版本。

```bash
PYTHONPATH=src .venv/bin/python scripts/migrate.py
```

也可以使用 `psql`：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/0001_job_data.sql
```

迁移启用 `pgcrypto` 与 `pg_trgm`。线上账号若没有创建扩展的权限，应由数据库管理员预先启用。

`raw_job_snapshots` 按月分区。采集任务会在写入前调用
`ensure_raw_job_snapshot_partition()`；默认分区仅用于异常情况下防止数据丢失，不应长期积累记录。
