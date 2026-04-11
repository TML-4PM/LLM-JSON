-- ============================================================
-- LLM-JSON Supabase Schema — S1 (lzfgigiyqpuuxslsygjt)
-- Schema: llm
-- Purpose: Derived, searchable, indexed layer only.
--          Raw corpus stays in S3. Never insert raw exports here.
-- RDTI: is_rd=true, project_code=LLM-JSON
-- ============================================================

CREATE SCHEMA IF NOT EXISTS llm;

-- ------------------------------------------------------------
-- 1. CORPUS REGISTRY
-- Source of truth for all raw S3 files.
-- Mirrors manifests/corpus_registry.json but queryable.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.corpus_registry (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  corpus_id           TEXT NOT NULL UNIQUE,               -- e.g. corpus_gpt_20260410_001
  provider            TEXT NOT NULL CHECK (provider IN ('gpt','claude','perplexity','gemini','grok','unknown')),
  s3_raw_uri          TEXT NOT NULL,
  s3_manifest_uri     TEXT,
  file_size_bytes     BIGINT,
  conversation_count  INTEGER,
  message_count       INTEGER,
  date_collected      DATE NOT NULL,
  date_processed      DATE,
  processing_status   TEXT NOT NULL DEFAULT 'pending'
                        CHECK (processing_status IN ('pending','chunking','analysing','complete','failed')),
  chunk_count         INTEGER,
  schema_version      TEXT NOT NULL DEFAULT '1.0',
  notes               TEXT,
  is_rd               BOOLEAN NOT NULL DEFAULT TRUE,
  project_code        TEXT NOT NULL DEFAULT 'LLM-JSON',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_corpus_registry_provider_date
  ON llm.corpus_registry (provider, date_collected);
CREATE INDEX IF NOT EXISTS idx_corpus_registry_status
  ON llm.corpus_registry (processing_status);

-- ------------------------------------------------------------
-- 2. CONVERSATIONS
-- One row per conversation extracted from a corpus file.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.conversations (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id     TEXT NOT NULL,                      -- provider-native ID
  corpus_id           TEXT NOT NULL REFERENCES llm.corpus_registry(corpus_id),
  provider            TEXT NOT NULL,
  date_conversation   DATE,
  message_count       INTEGER,
  title               TEXT,
  topics              TEXT[],                             -- extracted topic tags
  s3_chunk_uri        TEXT,                              -- which chunk contains this convo
  chunk_offset        INTEGER,                           -- message index offset within chunk
  summary             TEXT,                              -- 2–3 sentence summary
  has_learnings       BOOLEAN DEFAULT FALSE,
  learning_count      INTEGER DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_provider_id
  ON llm.conversations (provider, conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_corpus
  ON llm.conversations (corpus_id);
CREATE INDEX IF NOT EXISTS idx_conversations_date
  ON llm.conversations (date_conversation);

-- ------------------------------------------------------------
-- 3. CHUNKS
-- S3 chunk file registry — pointers only, not the data itself.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.chunks (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id            TEXT NOT NULL UNIQUE,              -- e.g. chunk_gpt_20260410_000001
  corpus_id           TEXT NOT NULL REFERENCES llm.corpus_registry(corpus_id),
  provider            TEXT NOT NULL,
  s3_uri              TEXT NOT NULL,
  chunk_sequence      INTEGER NOT NULL,
  message_start       INTEGER,
  message_end         INTEGER,
  size_bytes          BIGINT,
  conversation_ids    TEXT[],                            -- conversations spanned by this chunk
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_corpus ON llm.chunks (corpus_id);

-- ------------------------------------------------------------
-- 4. LEARNINGS
-- The core extraction table. One row per learning object.
-- Must conform to contracts/learning_schema.json.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.learnings (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  learning_id         TEXT NOT NULL UNIQUE,              -- lrn_YYYYMMDD_NNNNNN
  date                DATE NOT NULL,
  source_provider     TEXT NOT NULL CHECK (source_provider IN ('gpt','claude','perplexity','gemini','grok','unknown')),
  corpus_id           TEXT REFERENCES llm.corpus_registry(corpus_id),
  conversation_id     TEXT,
  chunk_ref           TEXT,
  message_span_start  INTEGER,
  message_span_end    INTEGER,
  learning_type       TEXT NOT NULL CHECK (learning_type IN (
                        'prompt_win','prompt_fail','workflow_win','workflow_fail',
                        'decision','commitment','blocker','contradiction',
                        'reusable_pattern','entity_reference','asset_reference',
                        'anti_pattern','model_strength','model_weakness',
                        'opportunity','canon_update_candidate','code_snippet','memory_candidate'
                      )),
  title               TEXT NOT NULL,
  summary             TEXT NOT NULL,
  evidence            JSONB NOT NULL DEFAULT '[]',
  reusability         TEXT NOT NULL CHECK (reusability IN ('high','medium','low','none')),
  confidence          NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  feed_forward        JSONB NOT NULL DEFAULT '{}',
  tags                TEXT[] NOT NULL DEFAULT '{}',
  provider_comparison JSONB,
  business_key        TEXT,
  project_code        TEXT DEFAULT 'LLM-JSON',
  is_rd               BOOLEAN NOT NULL DEFAULT TRUE,
  extracted_by        TEXT,
  schema_version      TEXT NOT NULL DEFAULT '1.0',
  promoted_to_memory  BOOLEAN DEFAULT FALSE,
  promoted_to_prompt  BOOLEAN DEFAULT FALSE,
  archived            BOOLEAN DEFAULT FALSE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learnings_date ON llm.learnings (date);
CREATE INDEX IF NOT EXISTS idx_learnings_type ON llm.learnings (learning_type);
CREATE INDEX IF NOT EXISTS idx_learnings_provider ON llm.learnings (source_provider);
CREATE INDEX IF NOT EXISTS idx_learnings_reusability ON llm.learnings (reusability);
CREATE INDEX IF NOT EXISTS idx_learnings_tags ON llm.learnings USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_learnings_feed_forward ON llm.learnings USING GIN (feed_forward);
CREATE INDEX IF NOT EXISTS idx_learnings_business_key ON llm.learnings (business_key);

-- Full-text search on title + summary
CREATE INDEX IF NOT EXISTS idx_learnings_fts ON llm.learnings
  USING GIN (to_tsvector('english', title || ' ' || summary));

-- ------------------------------------------------------------
-- 5. DAILY DIGEST
-- Compact row per date per provider + cross-llm summary.
-- Mirrors daily/YYYY-MM-DD/_index.json.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.daily_digest (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  date                  DATE NOT NULL,
  provider              TEXT NOT NULL,                   -- 'cross-llm' for aggregate row
  run_id                TEXT NOT NULL,
  conversation_count    INTEGER DEFAULT 0,
  message_count         INTEGER DEFAULT 0,
  learning_count        INTEGER DEFAULT 0,
  prompt_wins           INTEGER DEFAULT 0,
  prompt_fails          INTEGER DEFAULT 0,
  decisions_detected    INTEGER DEFAULT 0,
  tasks_created         INTEGER DEFAULT 0,
  contradictions        INTEGER DEFAULT 0,
  memory_candidates     INTEGER DEFAULT 0,
  top_tags              TEXT[],
  s3_analysis_prefix    TEXT,
  github_daily_path     TEXT,
  generated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT daily_digest_date_provider_pk UNIQUE (date, provider)
);

CREATE INDEX IF NOT EXISTS idx_daily_digest_date ON llm.daily_digest (date);

-- ------------------------------------------------------------
-- 6. PROMPT PATTERNS
-- Winning prompts worth reusing. Sourced from learnings.
-- Feed into prompts/ folder in repo.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.prompt_patterns (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pattern_id            TEXT NOT NULL UNIQUE,
  learning_id           TEXT REFERENCES llm.learnings(learning_id),
  provider              TEXT NOT NULL,
  pattern_type          TEXT NOT NULL CHECK (pattern_type IN (
                          'extraction','summarisation','classification',
                          'contradiction_detection','reuse_identification',
                          'task_detection','decision_detection','entity_extraction'
                        )),
  title                 TEXT NOT NULL,
  prompt_template       TEXT NOT NULL,                   -- the actual prompt or template
  outcome_description   TEXT NOT NULL,
  confidence            NUMERIC(4,3),
  use_count             INTEGER DEFAULT 0,
  last_used_at          TIMESTAMPTZ,
  tags                  TEXT[],
  is_canonical          BOOLEAN DEFAULT FALSE,
  is_deprecated         BOOLEAN DEFAULT FALSE,
  notes                 TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- 7. CONTRADICTIONS
-- Cross-provider conflicts on shared topics.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.contradictions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contradiction_id      TEXT NOT NULL UNIQUE,
  date                  DATE NOT NULL,
  topic                 TEXT NOT NULL,
  providers_compared    TEXT[] NOT NULL,
  position_a            TEXT NOT NULL,
  position_b            TEXT NOT NULL,
  agreement             TEXT NOT NULL CHECK (agreement IN ('agree','partial','contradict')),
  recommended_stance    TEXT,
  needs_human_review    BOOLEAN DEFAULT TRUE,
  resolved              BOOLEAN DEFAULT FALSE,
  resolution_notes      TEXT,
  learning_ids          TEXT[],                          -- source learnings
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contradictions_date ON llm.contradictions (date);
CREATE INDEX IF NOT EXISTS idx_contradictions_resolved ON llm.contradictions (resolved);

-- ------------------------------------------------------------
-- 8. ENTITIES
-- Named entity registry extracted from corpus.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.entities (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity                TEXT NOT NULL,
  entity_type           TEXT NOT NULL CHECK (entity_type IN (
                          'person','system','org','product','place','business_key','domain'
                        )),
  first_seen_date       DATE,
  last_seen_date        DATE,
  mention_count         INTEGER DEFAULT 1,
  providers_seen        TEXT[],
  is_canonical          BOOLEAN DEFAULT FALSE,
  notes                 TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT entities_entity_type_pk UNIQUE (entity, entity_type)
);

-- ------------------------------------------------------------
-- 9. CORPUS ANALYSIS JOBS
-- Audit trail for every analyse-in-place job run.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm.analysis_jobs (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id                TEXT NOT NULL UNIQUE,
  corpus_id             TEXT REFERENCES llm.corpus_registry(corpus_id),
  job_type              TEXT NOT NULL CHECK (job_type IN (
                          'analyse_in_place','build_daily_feed','publish_learnings','chunk','reprocess'
                        )),
  status                TEXT NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running','complete','failed','partial')),
  started_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at          TIMESTAMPTZ,
  duration_ms           INTEGER,
  chunks_processed      INTEGER DEFAULT 0,
  learnings_extracted   INTEGER DEFAULT 0,
  errors                JSONB DEFAULT '[]',
  lambda_request_id     TEXT,
  s3_output_prefix      TEXT,
  notes                 TEXT
);

-- ------------------------------------------------------------
-- RLS — enable on all tables, restrict to service role for now
-- Expand with user policies when multi-tenant access needed
-- ------------------------------------------------------------
ALTER TABLE llm.corpus_registry   ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.conversations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.chunks             ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.learnings          ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.daily_digest       ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.prompt_patterns    ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.contradictions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.entities           ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm.analysis_jobs      ENABLE ROW LEVEL SECURITY;

-- Service role bypass (already implicit in Supabase, explicit for clarity)
CREATE POLICY service_role_all ON llm.corpus_registry   FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.conversations      FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.chunks             FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.learnings          FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.daily_digest       FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.prompt_patterns    FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.contradictions     FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.entities           FOR ALL USING (true);
CREATE POLICY service_role_all ON llm.analysis_jobs      FOR ALL USING (true);

-- ------------------------------------------------------------
-- VIEWS
-- ------------------------------------------------------------

-- Hot learnings: high reusability, last 7 days, not yet promoted
CREATE OR REPLACE VIEW llm.v_hot_learnings AS
SELECT
  learning_id, date, source_provider, learning_type,
  title, summary, confidence, tags, feed_forward
FROM llm.learnings
WHERE reusability = 'high'
  AND archived = FALSE
  AND promoted_to_prompt = FALSE
  AND date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY confidence DESC, date DESC;

-- Daily summary across all providers
CREATE OR REPLACE VIEW llm.v_daily_summary AS
SELECT
  date,
  SUM(conversation_count)   AS total_conversations,
  SUM(message_count)         AS total_messages,
  SUM(learning_count)        AS total_learnings,
  SUM(prompt_wins)           AS total_prompt_wins,
  SUM(contradictions)        AS total_contradictions,
  SUM(memory_candidates)     AS total_memory_candidates,
  COUNT(DISTINCT provider)   AS providers_active
FROM llm.daily_digest
WHERE provider != 'cross-llm'
GROUP BY date
ORDER BY date DESC;

-- Open contradictions needing review
CREATE OR REPLACE VIEW llm.v_open_contradictions AS
SELECT *
FROM llm.contradictions
WHERE resolved = FALSE
  AND needs_human_review = TRUE
ORDER BY date DESC;

-- Learning feed-forward queue: should_update_prompt_pack=true, not yet promoted
CREATE OR REPLACE VIEW llm.v_prompt_pack_queue AS
SELECT
  learning_id, date, source_provider, learning_type,
  title, summary, confidence, tags
FROM llm.learnings
WHERE (feed_forward->>'should_update_prompt_pack')::boolean = TRUE
  AND promoted_to_prompt = FALSE
  AND archived = FALSE
ORDER BY confidence DESC, date DESC;

-- Memory candidate queue
CREATE OR REPLACE VIEW llm.v_memory_candidate_queue AS
SELECT
  learning_id, date, source_provider, title, summary, confidence, tags
FROM llm.learnings
WHERE learning_type = 'memory_candidate'
  AND promoted_to_memory = FALSE
  AND archived = FALSE
ORDER BY confidence DESC;

-- ------------------------------------------------------------
-- updated_at trigger
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION llm.fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_corpus_registry_updated_at
  BEFORE UPDATE ON llm.corpus_registry
  FOR EACH ROW EXECUTE FUNCTION llm.fn_set_updated_at();

CREATE TRIGGER trg_learnings_updated_at
  BEFORE UPDATE ON llm.learnings
  FOR EACH ROW EXECUTE FUNCTION llm.fn_set_updated_at();

CREATE TRIGGER trg_prompt_patterns_updated_at
  BEFORE UPDATE ON llm.prompt_patterns
  FOR EACH ROW EXECUTE FUNCTION llm.fn_set_updated_at();

CREATE TRIGGER trg_entities_updated_at
  BEFORE UPDATE ON llm.entities
  FOR EACH ROW EXECUTE FUNCTION llm.fn_set_updated_at();
