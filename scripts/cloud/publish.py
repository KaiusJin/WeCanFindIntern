"""Publish the committed public catalogue. The manifest is the last object written."""

import json
from datetime import UTC, datetime, timedelta

from wecanfindintern.job_feed import JOB_FIELDS, SOURCE_FIELDS, decode_package, encode_package


def collect_changes(db, previous_version):
    # Build and hash the allowlisted projection inside Postgres. Only changed rows
    # cross the Supabase network boundary. Observation clocks travel as metadata.
    fields = ", ".join(f"j.{col}" for col in JOB_FIELDS)
    source_fields = ", ".join(f"s.{col}" for col in SOURCE_FIELDS)
    with db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        db.execute("SET LOCAL TIME ZONE 'UTC'")
        db.execute(f"""
            CREATE TEMP VIEW feed_rows AS
            SELECT j.public_id, j.last_seen_at, src.observations,
                   to_jsonb(job) || jsonb_build_object('sources', src.sources) AS content
            FROM jobs j CROSS JOIN LATERAL (SELECT {fields}) job
            CROSS JOIN LATERAL (
                SELECT coalesce(jsonb_agg(to_jsonb(item) ORDER BY s.source_fingerprint),
                                '[]') AS sources,
                       jsonb_object_agg(encode(s.source_fingerprint,'hex'), jsonb_build_object(
                           'last_seen_at', s.last_seen_at, 'last_payload_hash', s.last_payload_hash
                       )) AS observations
                FROM job_sources s CROSS JOIN LATERAL (SELECT {source_fields}) item
                WHERE s.job_id=j.id
            ) src
        """)
        # A sequence survives deletion of the last job/tombstone and loss of the R2 manifest.
        seq = db.execute("SELECT nextval('feed_release_seq')").fetchone()[0]
        if seq <= previous_version:
            raise RuntimeError("Database sequence is behind the published feed")
        db.execute(
            """
            INSERT INTO feed_versions(public_id,content_hash,sync_seq)
            SELECT public_id, sha256(convert_to(content::text,'UTF8')), %s FROM feed_rows
            ON CONFLICT(public_id) DO UPDATE SET
                content_hash=excluded.content_hash, sync_seq=excluded.sync_seq
            WHERE feed_versions.content_hash IS DISTINCT FROM excluded.content_hash
        """,
            (seq,),
        )
        db.execute(
            """
            UPDATE feed_versions v SET content_hash=NULL, sync_seq=%s
            WHERE content_hash IS NOT NULL
              AND NOT EXISTS (SELECT FROM jobs j WHERE j.public_id=v.public_id)
        """,
            (seq,),
        )
        changes = db.execute(
            """
            SELECT v.public_id, r.content, r.last_seen_at, r.observations
            FROM feed_versions v LEFT JOIN feed_rows r USING(public_id)
            WHERE v.sync_seq > %s ORDER BY v.public_id
        """,
            (previous_version,),
        ).fetchall()
        db.execute("DROP VIEW pg_temp.feed_rows")
    rows = []
    for key, content, seen_at, observations in changes:
        row = {"public_id": str(key)}
        if content is None:
            row["deleted"] = True
        else:
            row.update(content, last_seen_at=seen_at.isoformat())
            for source in row["sources"]:
                source.update(observations[source["source_fingerprint"].removeprefix("\\x")])
        rows.append(row)
    return (seq if rows or not previous_version else previous_version), rows


class R2:
    def __init__(self, client, bucket):
        self.client = client
        self.bucket = bucket

    def read(self, key):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def put(self, key, body, *, manifest=False):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json" if manifest else "application/gzip",
            CacheControl="no-store" if manifest else "public,max-age=31536000,immutable",
        )

    def put_ops(self, key, body, content_type, *, content_encoding=None):
        """Operator report objects: always revalidated, never cached."""
        extra = {"ContentEncoding": content_encoding} if content_encoding else {}
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl="no-store",
            **extra,
        )

    def keep_newest(self, prefix, keep):
        """Delete all but the ``keep`` most recently written objects under ``prefix``."""
        paginator = self.client.get_paginator("list_objects_v2")
        objects = [
            obj
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
        ]
        objects.sort(key=lambda obj: obj["LastModified"], reverse=True)
        expired = [{"Key": obj["Key"]} for obj in objects[keep:]]
        for start in range(0, len(expired), 1000):
            self.client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": expired[start : start + 1000], "Quiet": True}
            )

    def prune(self, retained):
        cutoff = datetime.now(UTC) - timedelta(days=2)
        paginator = self.client.get_paginator("list_objects_v2")
        total_bytes = 0
        for prefix in ("snapshots/jobs-", "deltas/jobs-"):
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                expired = []
                for obj in page.get("Contents", []):
                    if obj["Key"] not in retained and obj["LastModified"] < cutoff:
                        expired.append({"Key": obj["Key"]})
                    else:
                        total_bytes += obj["Size"]
                if expired:
                    result = self.client.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": expired, "Quiet": True},
                    )
                    if result.get("Errors"):
                        raise RuntimeError("R2 package cleanup failed")
        return total_bytes


def publish(db, store):
    raw = store.read("manifest.json")
    prior = json.loads(raw) if raw is not None else None
    if prior and prior["format"] != 1:
        raise ValueError("Unsupported published feed format")
    base = prior["version"] if prior else 0
    version, changes = collect_changes(db, base)
    if not prior or changes:
        # Rebuild the current snapshot from R2 and the small database delta.
        # If the base object was lost, the database can regenerate it.
        previous = store.read(prior["snapshot"]["key"]) if prior else None
        if previous is None:
            _, full = collect_changes(db, 0) if prior else (version, changes)
            catalog = {r["public_id"]: r for r in full if not r.get("deleted")}
        else:
            catalog = {r["public_id"]: r for r in decode_package(previous, prior["snapshot"])}
            for row in changes:
                if row.get("deleted"):
                    catalog.pop(row["public_id"], None)
                else:
                    catalog[row["public_id"]] = row
        data, snapshot = encode_package(list(catalog.values()), "snapshots", version)
        store.put(snapshot["key"], data)
        deltas = []
        if prior:
            data, delta = encode_package(changes, "deltas", version)
            store.put(delta["key"], data)
            deltas = [*prior["deltas"], dict(delta, from_version=base)][-42:]
        prior = {"format": 1, "version": version, "snapshot": snapshot, "deltas": deltas}
        store.put("manifest.json", json.dumps(prior).encode(), manifest=True)

    # Any interruption above leaves the last manifest usable. Keep tombstones
    # until the manifest that acknowledges their sequence has been published.
    db.execute(
        "DELETE FROM feed_versions WHERE content_hash IS NULL AND sync_seq <= %s",
        (prior["version"],),
    )
    retained = {item["key"] for item in [prior["snapshot"], *prior["deltas"]]}
    stored = store.prune(retained)
    print(
        json.dumps(
            {
                "version": prior["version"],
                "changed_jobs": len(changes),
                "snapshot_jobs": prior["snapshot"]["count"],
                "snapshot_bytes": prior["snapshot"]["bytes"],
                "delta_bytes": prior["deltas"][-1]["bytes"] if changes and prior["deltas"] else 0,
                "r2_package_bytes": stored,
                "database_bytes": db.execute(
                    "SELECT pg_database_size(current_database())"
                ).fetchone()[0],
            }
        ),
        flush=True,
    )
    return prior


def r2_from_env():
    """R2 store from the workflow's credentials (cloud-only; desktop never imports boto3)."""
    import os

    import boto3

    return R2(
        boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT_URL"],
            region_name="auto",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        ),
        os.environ["R2_BUCKET"],
    )
