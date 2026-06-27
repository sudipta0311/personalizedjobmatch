-- schema.sql  — Neon (PostgreSQL) DDL for the job-search agent
-- Run once against your Neon database:
--   psql $NEON_DATABASE_URL -f agent/db/schema.sql
-- Safe to re-run (all CREATE TABLE … IF NOT EXISTS).

-- ---------------------------------------------------------------------------
-- jobs — one row per discovered job posting
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT NOT NULL,          -- 'greenhouse' | 'lever' | 'career_page'
    url             TEXT NOT NULL,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    location        TEXT,
    country         TEXT,                   -- ISO 3166-1 alpha-2 (best-effort parse)
    market_tag      TEXT,                   -- 'eu' | 'india' | 'gcc' | 'other'
    jd_text         TEXT,                   -- full job description text
    posted_date     DATE,
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT NOT NULL UNIQUE    -- SHA-256 of (company+norm_title+location)
);

CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at ON jobs (discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company       ON jobs (company);
CREATE INDEX IF NOT EXISTS idx_jobs_country       ON jobs (country);

-- ---------------------------------------------------------------------------
-- scores — LLM + rule scoring output per job
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scores (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    rule_flags      JSONB,          -- {auth_flag, grade_flag, market_tag, veto: bool}
    auth_fit        NUMERIC(5,2),   -- 0–100 numeric component
    grade_fit       NUMERIC(5,2),
    llm_fit         NUMERIC(5,2),   -- 0–100 from LLM
    composite       NUMERIC(5,2),   -- final weighted score
    rationale       TEXT,           -- one-line LLM rationale
    match_points    JSONB,          -- ["point1", "point2", "point3"]
    gaps            JSONB           -- ["gap1", "gap2"]
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_job_id ON scores (job_id);

-- ---------------------------------------------------------------------------
-- digests — one row per weekly digest email sent
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS digests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          TEXT NOT NULL,          -- GitHub Actions run ID or manual label
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    gmail_thread_id TEXT,                   -- Gmail thread ID of the sent digest
    gmail_message_id TEXT,                  -- Gmail message ID
    index_map       JSONB NOT NULL          -- {"1": "job_uuid", "2": "job_uuid", ...}
);

CREATE INDEX IF NOT EXISTS idx_digests_sent_at ON digests (sent_at DESC);

-- ---------------------------------------------------------------------------
-- commands — reply commands parsed from user emails (idempotency via message_id)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commands (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    digest_id       UUID REFERENCES digests (id) ON DELETE SET NULL,
    gmail_message_id TEXT NOT NULL UNIQUE,  -- dedup key — never process twice
    received_at     TIMESTAMPTZ,
    raw_text        TEXT,
    parsed          JSONB,      -- [{command, ids: [...]}, ...] or {command, id, question}
    processed_at    TIMESTAMPTZ             -- null = pending
);

CREATE INDEX IF NOT EXISTS idx_commands_processed ON commands (processed_at)
    WHERE processed_at IS NULL;

-- ---------------------------------------------------------------------------
-- applications — execution state per job (prepare / warm / track)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL UNIQUE REFERENCES jobs (id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'discovered',
    -- status values: discovered | shortlisted | emailed | skipped |
    --                prepared | warm_drafted | applied | interview |
    --                follow_up_due | closed
    cv_path         TEXT,           -- relative path or GCS/S3 ref to generated CV
    letter_path     TEXT,
    answers         JSONB,          -- form answers
    linkedin_play   JSONB,          -- {search_string, contacts, connection_note, follow_up}
    brief_path      TEXT,
    prepared_at     TIMESTAMPTZ,
    warm_drafted_at TIMESTAMPTZ,
    applied_at      TIMESTAMPTZ,
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications (status);

-- ---------------------------------------------------------------------------
-- events — append-only audit trail
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES jobs (id) ON DELETE SET NULL,
    type            TEXT NOT NULL,  -- 'discovered' | 'scored' | 'emailed' | 'command_received' | etc.
    payload         JSONB,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_job_id ON events (job_id);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events (ts DESC);
