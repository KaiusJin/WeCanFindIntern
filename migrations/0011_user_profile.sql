CREATE TABLE IF NOT EXISTS user_profiles (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_key VARCHAR(64) NOT NULL DEFAULT 'default' UNIQUE,
    full_name VARCHAR(200) NOT NULL DEFAULT '',
    preferred_name VARCHAR(120), headline VARCHAR(240), summary TEXT,
    email VARCHAR(320), phone VARCHAR(80), city VARCHAR(120),
    region VARCHAR(120), country VARCHAR(120), linkedin_url TEXT,
    github_url TEXT, portfolio_url TEXT,
    schema_version VARCHAR(32) NOT NULL DEFAULT 'profile.v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO user_profiles (profile_key) VALUES ('default')
ON CONFLICT (profile_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS profile_education (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_work_experience (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_projects (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_skills (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_certifications (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_languages (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS profile_awards (
    id BIGSERIAL PRIMARY KEY, public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resume_documents (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    source_type VARCHAR(16) NOT NULL CHECK (source_type IN ('pdf','latex')),
    media_type VARCHAR(100) NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 8388608),
    sha256 CHAR(64) NOT NULL,
    content BYTEA NOT NULL,
    extracted_text TEXT NOT NULL,
    parser_version VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','confirmed','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), confirmed_at TIMESTAMPTZ,
    UNIQUE(profile_id, sha256)
);

CREATE TABLE IF NOT EXISTS profile_imports (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    resume_id BIGINT NOT NULL REFERENCES resume_documents(id) ON DELETE CASCADE,
    parser_version VARCHAR(64) NOT NULL,
    parsed_payload JSONB NOT NULL, warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','confirmed','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), confirmed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS profile_versions (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    profile_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    source_import_id BIGINT REFERENCES profile_imports(id) ON DELETE SET NULL,
    schema_version VARCHAR(32) NOT NULL, snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_profile_education_order ON profile_education(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_profile_work_order ON profile_work_experience(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_profile_projects_order ON profile_projects(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_profile_skills_order ON profile_skills(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_profile_skills_name
    ON profile_skills(profile_id, lower(payload->>'normalized_name'));
CREATE INDEX IF NOT EXISTS idx_profile_certifications_order ON profile_certifications(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_profile_languages_order ON profile_languages(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_profile_awards_order ON profile_awards(profile_id, position);
CREATE INDEX IF NOT EXISTS idx_resume_documents_created ON resume_documents(profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_profile_imports_resume ON profile_imports(resume_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_profile_versions_created ON profile_versions(profile_id, created_at DESC);

