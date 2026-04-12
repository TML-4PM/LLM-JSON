# publish_learnings — Job Contract

**Lambda name:** `llm-json-publish-learnings`  
**Trigger:** EventBridge weekly `cron(0 17 ? * SUN *)` — 03:00 AEST Sunday  
**Also:** Manual bridge invoke  
**Runtime:** Python 3.12 | Timeout: 900s | Memory: 2048MB  
**RDTI:** `is_rd=true`, `project_code=LLM-JSON`

---

## Inputs

### EventBridge (primary)
No payload — uses `lookback_days=7`.

### Manual bridge invoke
```json
{
  "lookback_days": 14,
  "source": "manual"
}
```

---

## Processing steps

```
1. Fetch prompt_win learnings (confidence >= 0.80, not yet promoted) from Supabase
2. For each: write prompt MD file to prompts/{tag}/{slug}.md in GitHub
3. Mark promoted_to_prompt=true in Supabase
4. Fetch memory_candidate learnings (confidence >= 0.85, not yet promoted)
5. Send Telegram digest to Troy for review (max 10 per run)
6. Fetch learnings per provider, build provider profile JSON
7. Write manifests/providers/{provider}.json to GitHub
8. Archive stale learnings (>90 days, confidence<0.5)
9. Notify Telegram summary
```

---

## Outputs

| Destination | Files |
|-------------|-------|
| GitHub `prompts/{tag}/` | `{slug}.md` per promoted prompt win |
| GitHub `manifests/providers/` | `{gpt,claude,perplexity,gemini,grok}.json` |
| Supabase | `llm.learnings.promoted_to_prompt=true` |
| Supabase | `llm.learnings.archived=true` (stale) |
| Telegram | Memory candidate digest for Troy review |

---

## Thresholds (configurable via env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `PROMPT_PACK_THRESHOLD` | `0.80` | Min confidence to promote to prompt pack |
| `MEMORY_CANDIDATE_THRESHOLD` | `0.85` | Min confidence to queue for memory review |
| `LOOKBACK_DAYS` | `7` | How many days back to scan |

---

## Kill switch

```bash
aws ssm put-parameter --name /t4h/llm-json/kill-switch-enabled --value true --overwrite
```
