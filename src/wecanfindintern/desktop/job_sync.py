"""Pull public packages over HTTPS and commit each verified package with its cursor."""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import psycopg
from psycopg.types.json import Jsonb

from wecanfindintern.job_feed import JOB_FIELDS, SOURCE_FIELDS, decode_package


def _columns(fields):
    return ", ".join(fields)


def _updates(fields):
    return ", ".join(f"{field}=excluded.{field}" for field in fields)


_JOB_COLUMNS = [*JOB_FIELDS, "last_seen_at"]
_UPSERT_JOB = f"""
    INSERT INTO jobs (cloud_id, public_id, {_columns(_JOB_COLUMNS)}, last_verified_at, source_count)
    SELECT %s, coalesce((SELECT public_id FROM jobs WHERE cloud_id=%s), j.public_id),
           {_columns(_JOB_COLUMNS)}, last_seen_at, %s
    FROM jsonb_populate_record(NULL::jobs, %s) j
    ON CONFLICT(cloud_id) DO UPDATE SET {_updates(_JOB_COLUMNS)},
        last_verified_at=excluded.last_verified_at, source_count=excluded.source_count,
        updated_at=now()
    RETURNING id
"""
_SOURCE_COLUMNS = [*SOURCE_FIELDS, "last_seen_at", "last_payload_hash"]
_UPSERT_SOURCES = f"""
    INSERT INTO job_sources (job_id, {_columns(_SOURCE_COLUMNS)},
                             last_scraped_at)
    SELECT %s, {_columns(_SOURCE_COLUMNS)}, last_seen_at
    FROM jsonb_populate_recordset(NULL::job_sources, %s) s
    ON CONFLICT(source_fingerprint) DO UPDATE SET job_id=excluded.job_id,
        {_updates(_SOURCE_COLUMNS)}, last_scraped_at=excluded.last_scraped_at, updated_at=now()
"""


async def _upsert(db, row):
    key = UUID(row["public_id"])
    sources = row["sources"]
    fingerprints = [bytes.fromhex(s["source_fingerprint"].removeprefix("\\x")) for s in sources]
    # Adopt a legacy public JobSpy identity, retaining its bookmarks and local UUID.
    # A cloud merge can fold more than one legacy job into this record.
    matches = await db.execute(
        """
        SELECT DISTINCT j.id, j.cloud_id FROM jobs j JOIN job_sources s ON s.job_id=j.id
        WHERE s.source_fingerprint=ANY(%s) ORDER BY j.id
        """,
        (fingerprints,),
    )
    owners = await matches.fetchall()
    for local_id, cloud_id in owners:
        if cloud_id is None:
            await db.execute(
                """
                UPDATE jobs SET cloud_id=%s WHERE id=%s
                  AND NOT EXISTS (SELECT FROM jobs WHERE cloud_id=%s)
            """,
                (key, local_id, key),
            )
            break
    job = {k: row[k] for k in ["public_id", *_JOB_COLUMNS]}
    inserted = await db.execute(_UPSERT_JOB, (key, key, len(sources), Jsonb(job)))
    (job_id,) = await inserted.fetchone()
    await db.execute(_UPSERT_SOURCES, (job_id, Jsonb(sources)))
    await db.execute(
        """
        DELETE FROM job_sources WHERE job_id=%s AND NOT (source_fingerprint=ANY(%s))
    """,
        (job_id, fingerprints),
    )
    displaced = [owner for owner, _ in owners if owner != job_id]
    if displaced:
        await db.execute(
            """
            UPDATE jobs j SET status=4, closed_at=now(), source_count=0, updated_at=now()
            WHERE id=ANY(%s) AND NOT EXISTS (SELECT FROM job_sources s WHERE s.job_id=j.id)
        """,
            (displaced,),
        )


async def apply_package(db, rows, package, *, snapshot):
    version = package["version"]
    keys = [UUID(row["public_id"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate job identity in package")
    async with db.transaction():
        cursor = (
            await (await db.execute("SELECT version FROM public_job_sync FOR UPDATE")).fetchone()
        )[0]
        if cursor >= version:
            return 0
        if not snapshot and package["from_version"] != cursor:
            raise ValueError("Delta does not follow the committed cursor")
        deleted = []
        for row in rows:
            if row.get("deleted"):
                if snapshot:
                    raise ValueError("Unexpected tombstone in snapshot")
                deleted.append(UUID(row["public_id"]))
            else:
                await _upsert(db, row)
        # Archiving a removed public job keeps existing tracker/agent references intact.
        if snapshot or deleted:
            predicate = "cloud_id <> ALL(%s)" if snapshot else "cloud_id=ANY(%s)"
            await db.execute(
                f"""
                UPDATE jobs SET status=4, closed_at=coalesce(closed_at,now()), updated_at=now()
                WHERE cloud_id IS NOT NULL AND status<>4 AND {predicate}
            """,
                (keys if snapshot else deleted,),
            )
        await db.execute("UPDATE public_job_sync SET version=%s", (version,))
    return len(rows)


async def _download(client, base, package, cache, folder):
    digest = package["sha256"]
    if len(digest) != 64 or len(bytes.fromhex(digest)) != 32:
        raise ValueError("Invalid package digest")
    key = f"{folder}/jobs-{int(package['version'])}-{digest}.json.gz"
    if package["key"] != key:
        raise ValueError("Invalid package path")
    path = cache / f"{digest}.part"
    size = package["bytes"]
    offset = path.stat().st_size if path.exists() else 0
    if offset > size:
        path.unlink()
        offset = 0
    if offset != size:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        async with client.stream("GET", f"{base}/{key}", headers=headers) as response:
            response.raise_for_status()
            append = response.status_code == 206
            if (
                append
                and response.headers.get("Content-Range") != f"bytes {offset}-{size - 1}/{size}"
            ):
                path.unlink(missing_ok=True)
                raise ValueError("Unexpected package byte range")
            with path.open("ab" if append else "wb") as stream:
                async for chunk in response.aiter_bytes():
                    stream.write(chunk)
                    if stream.tell() > size:
                        raise ValueError("Downloaded package exceeds declared size")

    if path.stat().st_size < size:
        raise OSError("Incomplete public job download; saved for resume")

    def verify():
        try:
            return decode_package(path.read_bytes(), package)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    return await asyncio.to_thread(verify), path


async def sync_feed(manifest_url, cache: Path):
    if not manifest_url:
        raise ValueError("Configure WCFI_PUBLIC_JOBS_MANIFEST_URL for public job sync")
    if urlsplit(manifest_url).scheme != "https":
        raise ValueError("The public job manifest requires HTTPS")
    cache.mkdir(parents=True, exist_ok=True)
    base = manifest_url.rsplit("/", 1)[0]
    async with httpx.AsyncClient(timeout=60, headers={"Accept-Encoding": "identity"}) as client:
        response = await client.get(manifest_url, headers={"Cache-Control": "no-cache"})
        response.raise_for_status()
        manifest = response.json()
        target = manifest["version"]
        snapshot = manifest["snapshot"]
        if manifest["format"] != 1 or snapshot["version"] != target or target < 1:
            raise ValueError("Unsupported or inconsistent public job manifest")
        async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URL"], autocommit=True
        ) as db:
            cursor = (await (await db.execute("SELECT version FROM public_job_sync")).fetchone())[0]
            applied = 0
            deltas = {p["from_version"]: p for p in manifest["deltas"]}
            while cursor < target:
                delta = deltas.get(cursor) if cursor else None
                if delta and cursor < delta["version"] <= target:
                    try:
                        rows, path = await _download(client, base, delta, cache, "deltas")
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code not in (404, 410):
                            raise
                    except ValueError:
                        pass  # A corrupt delta can be repaired by the verified current snapshot.
                    else:
                        applied += await apply_package(db, rows, delta, snapshot=False)
                        cursor = delta["version"]
                        path.unlink(missing_ok=True)
                        continue
                rows, path = await _download(client, base, snapshot, cache, "snapshots")
                applied += await apply_package(db, rows, snapshot, snapshot=True)
                cursor = target
                path.unlink(missing_ok=True)
    for stale in cache.glob("*.part"):
        stale.unlink(missing_ok=True)
    return {"status": "success", "version": cursor, "records_applied": applied}
