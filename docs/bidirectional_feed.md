# Bidirectional Learning Feed — Design Spec

**Version:** 1.0  
**Repo:** TML-4PM/LLM-JSON  
**Updated:** 2026-04-10

---

## What this is

The LLM corpus is not a one-way archive. It is a living intelligence loop.

Raw conversations → distilled learnings → fed back into every future run.

This document specifies how the two directions work.

---

## Direction 1: Raw corpus → Distilled learnings

### What gets extracted

| Learning type | Where it goes |
|---------------|---------------|
| `prompt_win` | `daily/prompt_wins.json`, `llm.prompt_patterns`, `prompts/` folder |
| `prompt_fail` | `daily/failure_patterns.json` |
| `workflow_win` | `daily/reusable_patterns.json` |
| `workflow_fail` | `daily/failure_patterns.json` |
| `decision` | `daily/decisions_detected.json`, `llm.learnings` |
| `commitment` | `daily/decisions_detected.json` |
| `blocker` | `daily/next_best_actions.json` |
| `contradiction` | `daily/contradictions.json`, `llm.contradictions` |
| `reusable_pattern` | `daily/reusable_patterns.json`, `prompts/` |
| `entity_reference` | `daily/entities.json`, `llm.entities` |
| `asset_reference` | `daily/urls_and_assets.json` |
| `anti_pattern` | `daily/failure_patterns.json` |
| `model_strength` | `daily/provider_deltas/{provider}.json` |
| `model_weakness` | `daily/provider_deltas/{provider}.json` |
| `opportunity` | `daily/next_best_actions.json` |
| `canon_update_candidate` | `daily/memory_candidates.json`, reviewed by Troy |
| `code_snippet` | `daily/reusable_patterns.json` |
| `memory_candidate` | `daily/memory_candidates.json`, `llm.v_memory_candidate_queue` |

### Questions the daily folder must answer

Every daily feed must answer all of these without requiring access to S3 raw:

1. What mattered today?
2. What changed vs yesterday?
3. What was learned?
4. What worked?
5. What failed?
6. What got decided?
7. What should be reused?
8. What should be avoided?
9. What should happen next?
10. Where do the LLMs disagree?

---

## Direction 2: Distilled learnings → Future corpus inputs

### Feed-forward channels

Learnings flow back into future work through these channels:

#### A. Prompt packs
- Source: `llm.prompt_patterns` WHERE `is_canonical=true`
- Destination: `prompts/` folder in this repo
- Update trigger: `feed_forward.should_update_prompt_pack = true`
- Process: `publish_learnings` Lambda promotes pattern → writes to `prompts/{type}/{slug}.md`

#### B. Provider-specific operating profiles
- Source: `daily/provider_deltas/*.json` aggregated over 30 days
- Destination: `manifests/providers/{provider}.json`
- Contents: recommended prompt types, known weaknesses, optimal use cases, contradiction rate
- Use: every new analysis job reads provider profile before running extraction

#### C. Canon update candidates
- Source: `llm.v_memory_candidate_queue`
- Destination: Troy review queue (Telegram notification)
- Confirmed → written to standing memory
- Format must match existing memory instruction pattern

#### D. Anti-pattern registry
- Source: `llm.learnings` WHERE `learning_type IN ('prompt_fail','anti_pattern','workflow_fail')`
- Destination: `prompts/` folder as negative examples
- Use: injected into extraction prompts as "avoid this" context

#### E. Issue templates
- Source: `daily/tasks_created.json` WHERE `confidence > 0.85`
- Destination: GitHub Issues on TML-4PM/LLM-JSON (if task warrants it)
- Trigger: `feed_forward.should_create_task = true`

#### F. Retrieval snippets
- Source: `llm.learnings` WHERE `reusability = 'high'`
- Destination: Supabase vector embeddings (Phase 2)
- Use: semantic retrieval across corpus for future analysis runs

#### G. "What to try next" playbooks
- Source: `daily/next_best_actions.json` aggregated
- Destination: `docs/playbooks/` (written by `build_daily_feed`)
- Contents: ordered action recommendations, urgency, assignee, rationale

#### H. Standing memory candidates
- Source: `llm.v_memory_candidate_queue`
- Process: Weekly sweep → Telegram summary to Troy → approve/reject

---

## Access model for LLMs

### Tier 1: Universal shared layer (all LLMs always read this)
```
daily/latest/cross_llm_summary.md
daily/latest/reusable_patterns.json
daily/latest/prompt_wins.json
daily/latest/next_best_actions.json
indexes/latest.json
manifests/corpus_registry.json
manifests/providers/{provider}.json
```

### Tier 2: Date-specific drill-down
```
daily/{YYYY-MM-DD}/_index.json
daily/{YYYY-MM-DD}/*.json
daily/{YYYY-MM-DD}/provider_deltas/*.json
```

### Tier 3: Deep retrieval (signed URL or API)
```
llm.learnings WHERE learning_type=X — Supabase RPC
llm.conversations WHERE topic LIKE '%X%' — Supabase RPC
S3 chunk lookup — signed URL from API endpoint
Semantic search — embedding vector lookup (Phase 2)
```

LLMs never get direct S3 bucket browse access.  
Heavy objects stay in S3.  
Compact intelligence layer stays in GitHub + Supabase.

---

## Bidirectional loop diagram

```
┌─────────────────────────────────────────────────────────┐
│                    RAW CORPUS (S3)                       │
│  provider=gpt|claude|perplexity|gemini|grok             │
│  1.7 GB+, JSONL.GZ, Hive-partitioned by date            │
└───────────────────────┬─────────────────────────────────┘
                        │  analyse_in_place Lambda
                        │  stream → chunk → extract
                        ▼
┌─────────────────────────────────────────────────────────┐
│              EXTRACTED LEARNINGS (Supabase)             │
│  llm.learnings / llm.conversations / llm.chunks         │
│  llm.prompt_patterns / llm.contradictions               │
└──────────┬────────────────────────────┬─────────────────┘
           │ build_daily_feed Lambda    │ publish_learnings
           ▼                            ▼
┌──────────────────┐          ┌─────────────────────────┐
│  DAILY FEED      │          │  PROMPT PACKS + PROFILES│
│  GitHub          │          │  GitHub prompts/         │
│  daily/YYYY-MM-DD│          │  manifests/providers/   │
│  daily/latest/   │          │  docs/playbooks/        │
└──────────┬───────┘          └─────────────────────────┘
           │                            │
           │  ALL LLMs read             │  ALL LLMs apply
           │  daily/latest first        │  before next run
           │                            │
           └────────────────────────────┘
                        │
                        ▼
              NEXT ANALYSIS RUN
              ↑ better prompts
              ↑ provider profiles
              ↑ anti-patterns avoided
              ↑ canon enforced
```

---

## Learning lifecycle

```
EXTRACTED → VALIDATED → STORED (Supabase)
         → (if prompt_win) → PROMOTED to prompts/
         → (if memory_candidate) → QUEUED for Troy review
         → (if contradiction) → FLAGGED in contradictions table
         → (if task) → QUEUED for issue creation
         → (after 90 days, low confidence) → ARCHIVED (never deleted)
```

---

## Phase roadmap

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Architecture defined, contracts written | ✅ Done |
| 1 | S3 bucket structure + corpus_registry | Next |
| 2 | analyse_in_place Lambda deployed | Next |
| 3 | Daily feed GitHub writes live | Next |
| 4 | Supabase schema deployed + upserts | Next |
| 5 | Telegram notifications for daily summary | Next |
| 6 | Prompt pack promotion automated | Phase 2 |
| 7 | Vector embeddings + semantic retrieval | Phase 2 |
| 8 | Cross-LLM contradiction alerting | Phase 2 |
| 9 | Memory candidate weekly sweep | Phase 2 |
