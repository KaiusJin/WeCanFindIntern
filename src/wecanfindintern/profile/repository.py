"""PostgreSQL persistence for the single-user Profile MVP."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from wecanfindintern.profile.models import (
    AwardEntry,
    CertificationEntry,
    EducationEntry,
    LanguageEntry,
    ProfileBasics,
    ProfilePayload,
    ProjectEntry,
    ResumeDocumentSummary,
    SkillEntry,
    UserProfile,
    WorkEntry,
)

SECTION_TABLES = {
    "education": ("profile_education", EducationEntry),
    "work_experience": ("profile_work_experience", WorkEntry),
    "projects": ("profile_projects", ProjectEntry),
    "skills": ("profile_skills", SkillEntry),
    "certifications": ("profile_certifications", CertificationEntry),
    "languages": ("profile_languages", LanguageEntry),
    "awards": ("profile_awards", AwardEntry),
}


class ProfileRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def _profile_row(self, connection: Any) -> dict[str, Any]:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """INSERT INTO user_profiles (profile_key) VALUES ('default')
                ON CONFLICT (profile_key) DO UPDATE SET profile_key=EXCLUDED.profile_key
                RETURNING *;"""
            )
            return await cursor.fetchone()

    @staticmethod
    def _completion(payload: ProfilePayload) -> int:
        checks = [
            bool(payload.basics.full_name),
            bool(payload.basics.email),
            bool(payload.education),
            bool(payload.work_experience),
            bool(payload.projects),
            bool(payload.skills),
            bool(payload.certifications),
            bool(payload.languages),
            bool(payload.awards),
        ]
        return round(sum(checks) / len(checks) * 100)

    async def get_profile(self) -> UserProfile:
        async with self.pool.connection() as connection:
            row = await self._profile_row(connection)
            sections: dict[str, list[Any]] = {}
            async with connection.cursor(row_factory=dict_row) as cursor:
                for name, (table, model) in SECTION_TABLES.items():
                    await cursor.execute(
                        f"SELECT public_id, payload FROM {table} "
                        "WHERE profile_id=%s ORDER BY position,id;",
                        (row["id"],),
                    )
                    entries = []
                    for item in await cursor.fetchall():
                        entries.append(
                            model.model_validate({**item["payload"], "id": item["public_id"]})
                        )
                    sections[name] = entries
        payload = ProfilePayload(basics=ProfileBasics.model_validate(row), **sections)
        return UserProfile(
            id=row["public_id"],
            **payload.model_dump(),
            completion_percent=self._completion(payload),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def _save_profile_on_connection(
        self, connection: Any, payload: ProfilePayload
    ) -> None:
        """Replace the current profile while preserving stable section UUIDs."""

        row = await self._profile_row(connection)
        basics = payload.basics
        await connection.execute(
            """UPDATE user_profiles SET full_name=%s,preferred_name=%s,
            email=%s,phone=%s,city=%s,region=%s,country=%s,
            linkedin_url=%s,github_url=%s,portfolio_url=%s,schema_version=%s,
            updated_at=now() WHERE id=%s;""",
            (
                basics.full_name,
                basics.preferred_name,
                basics.email,
                basics.phone,
                basics.city,
                basics.region,
                basics.country,
                basics.linkedin_url,
                basics.github_url,
                basics.portfolio_url,
                payload.schema_version,
                row["id"],
            ),
        )
        for name, (table, _) in SECTION_TABLES.items():
            retained_ids: list[UUID] = []
            for position, item in enumerate(getattr(payload, name)):
                item_data = item.model_dump(mode="json", exclude={"id"})
                item_id = getattr(item, "id", None)
                updated = None
                if item_id is not None:
                    updated = await (
                        await connection.execute(
                            f"""UPDATE {table}
                            SET position=%s,payload=%s,updated_at=now()
                            WHERE profile_id=%s AND public_id=%s
                            RETURNING public_id;""",
                            (position, Jsonb(item_data), row["id"], item_id),
                        )
                    ).fetchone()
                if updated is None:
                    updated = await (
                        await connection.execute(
                            f"""INSERT INTO {table} (profile_id,position,payload)
                            VALUES (%s,%s,%s) RETURNING public_id;""",
                            (row["id"], position, Jsonb(item_data)),
                        )
                    ).fetchone()
                retained_ids.append(updated["public_id"])
            if retained_ids:
                await connection.execute(
                    f"DELETE FROM {table} WHERE profile_id=%s AND NOT (public_id=ANY(%s));",
                    (row["id"], retained_ids),
                )
            else:
                await connection.execute(
                    f"DELETE FROM {table} WHERE profile_id=%s;", (row["id"],)
                )

    async def save_profile(self, payload: ProfilePayload) -> UserProfile:
        async with self.pool.connection() as connection, connection.transaction():
            await self._save_profile_on_connection(connection, payload)
        return await self.get_profile()

    async def create_resume_import(
        self,
        *,
        filename: str,
        source_type: str,
        media_type: str,
        content: bytes,
        extracted_text: str,
        parser_version: str,
        payload: ProfilePayload,
        warnings: list[str],
    ) -> tuple[ResumeDocumentSummary, UUID]:
        digest = sha256(content).hexdigest()
        async with self.pool.connection() as connection, connection.transaction():
            profile = await self._profile_row(connection)
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """INSERT INTO resume_documents
                    (profile_id,filename,source_type,media_type,size_bytes,sha256,content,
                    extracted_text,parser_version,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft')
                    ON CONFLICT (profile_id,sha256) DO UPDATE SET
                    filename=EXCLUDED.filename,source_type=EXCLUDED.source_type,
                    media_type=EXCLUDED.media_type,size_bytes=EXCLUDED.size_bytes,
                    content=EXCLUDED.content,extracted_text=EXCLUDED.extracted_text,
                    parser_version=EXCLUDED.parser_version,status='draft',confirmed_at=NULL
                    RETURNING id,public_id,filename,source_type,media_type,size_bytes,sha256,
                    parser_version,status,created_at,confirmed_at;""",
                    (
                        profile["id"],
                        filename,
                        source_type,
                        media_type,
                        len(content),
                        digest,
                        content,
                        extracted_text,
                        parser_version,
                    ),
                )
                resume = await cursor.fetchone()
                await cursor.execute(
                    """INSERT INTO profile_imports
                    (profile_id,resume_id,parser_version,parsed_payload,warnings,status)
                    VALUES (%s,%s,%s,%s,%s,'draft') RETURNING public_id;""",
                    (
                        profile["id"],
                        resume["id"],
                        parser_version,
                        Jsonb(payload.model_dump(mode="json")),
                        Jsonb(warnings),
                    ),
                )
                import_row = await cursor.fetchone()
        return (
            ResumeDocumentSummary.model_validate({**resume, "id": resume["public_id"]}),
            import_row["public_id"],
        )

    async def list_resumes(self) -> list[ResumeDocumentSummary]:
        async with self.pool.connection() as connection:
            profile = await self._profile_row(connection)
            result = await connection.execute(
                """SELECT public_id AS id,filename,source_type,media_type,size_bytes,sha256,
                parser_version,status,created_at,confirmed_at FROM resume_documents
                WHERE profile_id=%s ORDER BY created_at DESC;""",
                (profile["id"],),
            )
            return [ResumeDocumentSummary.model_validate(row) for row in await result.fetchall()]

    async def update_import_draft(
        self, import_id: UUID, payload: ProfilePayload
    ) -> ProfilePayload | None:
        """Autosave an import review without applying it to the live profile."""

        async with self.pool.connection() as connection, connection.transaction():
            result = await connection.execute(
                """UPDATE profile_imports SET parsed_payload=%s
                WHERE public_id=%s AND status='draft'
                RETURNING public_id;""",
                (Jsonb(payload.model_dump(mode="json")), import_id),
            )
            return payload if await result.fetchone() else None

    async def confirm_import(
        self, import_id: UUID, payload: ProfilePayload | None
    ) -> UserProfile | None:
        async with self.pool.connection() as connection, connection.transaction():
            imported = await (
                await connection.execute(
                    """SELECT id,parsed_payload FROM profile_imports
                    WHERE public_id=%s AND status='draft' FOR UPDATE;""",
                    (import_id,),
                )
            ).fetchone()
            if not imported:
                return None
            internal_id = imported["id"]
            final_payload = payload or ProfilePayload.model_validate(
                imported["parsed_payload"]
            )
            await self._save_profile_on_connection(connection, final_payload)
            await connection.execute(
                "UPDATE profile_imports SET status='confirmed',confirmed_at=now() WHERE id=%s;",
                (internal_id,),
            )
            await connection.execute(
                """UPDATE resume_documents SET status='confirmed',confirmed_at=now()
                WHERE id=(SELECT resume_id FROM profile_imports WHERE id=%s);""",
                (internal_id,),
            )
        return await self.get_profile()

    async def delete_resume(self, resume_id: UUID) -> bool:
        async with self.pool.connection() as connection, connection.transaction():
            result = await connection.execute(
                "DELETE FROM resume_documents WHERE public_id=%s RETURNING id;", (resume_id,)
            )
            return await result.fetchone() is not None
