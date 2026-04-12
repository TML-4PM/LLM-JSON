# build_daily_feed — Job Contract

**Lambda name:** `llm-json-build-daily-feed`  
**Trigger:** EventBridge daily `cron(0 16 * * ? *)` — 02:00 AEST  
**Also invoked by:** `analyse_in_place` on completion (async Event)  
**Runtime:** Python 3.12 | Timeout: 900s | Memory: 2048MB  
**RDTI:** `is_rd=true`, `project_code=LLM-JSON`

---

## Inputs

### EventBridge (primary)
No payload — uses today's date.

### Invoke from analyse_in_place / manual bridge
```json
{
  "date": "2026-04-10",
  "provider": "gpt",
  "source": "analyse_in_place"
}
```

---

## Processing steps

```
1. Fetch all learnings for date from llm.learnings (Supabase)
2. Aggregate into all daily output files per daily_schema.json
3. Write daily/YYYY-MM-DD/* to GitHub (12+ files)
4. Overwrite daily/latest/* in GitHub (4 files)
5. Update indexes/latest.json in GitHub
6. Upsert llm.daily_digest in Supabase (one row per provider + cross-llm)
7. Notify via Telegram: date, learning_count, contradiction_count, top_action
```

---

## Outputs

| Destination | Files |
|-------------|-------|
| GitHub `daily/YYYY-MM-DD/` | `_index.json`, `cross_llm_summary.md`, `executive_brief.md`, `reusable_patterns.json`, `prompt_wins.json`, `failure_patterns.json`, `contradictions.json`, `tasks_created.json`, `decisions_detected.json`, `memory_candidates.json`, `next_best_actions.json`, `provider_deltas/*.json` |
| GitHub `daily/latest/` | `cross_llm_summary.md`, `reusable_patterns.json`, `prompt_wins.json`, `next_best_actions.json` |
| GitHub `indexes/` | `latest.json` |
| Supabase | `llm.daily_digest` (upsert by date+provider) |

---

## Error handling

| Error | Impact | Action |
|-------|--------|--------|
| Supabase fetch fails | No learnings — empty daily | Log, write empty daily, continue |
| GitHub PUT fails | File not written | Retry 3x, log to CloudWatch |
| Individual file failure | Other files unaffected | Continue, log error |
| Complete failure | No daily feed | Alert Telegram, mark partial |

---

## Kill switch

```bash
# SSM
aws ssm put-parameter --name /t4h/llm-json/kill-switch-enabled --value true --overwrite
# Or disable EventBridge rule
aws events disable-rule --name llm-json-daily-feed-schedule
```
