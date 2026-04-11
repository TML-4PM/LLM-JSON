# analyse_in_place — Job Contract

**Lambda name:** `llm-json-analyse-in-place`  
**Trigger:** S3 PutObject on `t4h-llm-json/llm-json/raw/**`  
**Fallback trigger:** Manual bridge invoke via `zdgnab3py0`  
**Runtime:** Python 3.12  
**Timeout:** 900s  
**Memory:** 2048MB  
**RDTI:** `is_rd=true`, `project_code=LLM-JSON`

---

## Inputs

### S3 event trigger (primary)
```json
{
  "Records": [{
    "s3": {
      "bucket": { "name": "t4h-llm-json" },
      "object": { "key": "llm-json/raw/provider=gpt/year=2026/month=04/day=10/export.jsonl.gz" }
    }
  }]
}
```

### Manual bridge invoke
```json
{
  "action": "invoke_function",
  "function_name": "llm-json-analyse-in-place",
  "payload": {
    "s3_uri": "s3://t4h-llm-json/llm-json/raw/provider=gpt/year=2026/month=04/day=10/export.jsonl.gz",
    "provider": "gpt",
    "date": "2026-04-10",
    "force_reprocess": false,
    "dry_run": false,
    "request_id": "req_20260410_001",
    "source": "bridge"
  }
}
```

---

## Processing steps (in order)

```
1. VALIDATE
   - Confirm S3 object exists and is readable
   - Parse provider and date from S3 key path
   - Check corpus_registry: if already complete and force_reprocess=false → skip
   - Register job in llm.analysis_jobs (status=running)

2. MANIFEST
   - Read manifest.json alongside raw file (if exists)
   - If missing: generate minimal manifest from file metadata
   - Write back to s3://.../raw/.../manifest.json

3. CHUNK
   - Stream-read raw file (never load full 1.7GB into memory)
   - Split into ~500 message chunks (~50MB each compressed)
   - Write chunks → s3://.../chunks/provider=X/year/month/day/chunk-NNNNNN.jsonl.gz
   - Register each chunk in llm.chunks

4. CLASSIFY CONVERSATIONS
   - Per conversation in each chunk:
     - Extract conversation_id, message_count, date
     - Generate 2–3 sentence summary (via Bedrock Claude Haiku, cheap)
     - Extract topic tags (max 5)
     - Insert into llm.conversations

5. EXTRACT LEARNINGS
   - Per conversation:
     - Run extraction prompts from prompts/extraction/
     - Extract: decisions, tasks, prompt_wins, failures, contradictions, entities, assets, memory_candidates
     - Score reusability and confidence
     - Validate against learning_schema.json
     - Batch insert into llm.learnings

6. WRITE ANALYSIS OUTPUTS
   - Write per-provider analysis to s3://.../analysis/provider=X/year/month/day/
   - Write cross-llm analysis to s3://.../analysis/cross-llm/year/month/day/

7. BUILD DAILY FEED
   - Aggregate learnings for the date
   - Write all daily/ files per daily_schema.json contract
   - Write to GitHub daily/YYYY-MM-DD/ via GitHub API (PAT_2 or PAT_3)
   - Copy to daily/latest/
   - Update indexes/latest.json

8. SYNC TO SUPABASE
   - Upsert llm.daily_digest rows (one per provider + cross-llm)
   - Mark corpus_registry.processing_status = 'complete'
   - Update analysis_jobs (status=complete, duration_ms, counts)

9. NOTIFY
   - Send Telegram notification: date, provider, learning_count, contradictions, next_best_actions[0]
```

---

## Outputs

| Destination | Path | Contents |
|-------------|------|----------|
| S3 chunks | `.../chunks/provider=X/.../chunk-NNNNNN.jsonl.gz` | Chunked raw conversations |
| S3 analysis | `.../analysis/provider=X/.../*.json` | Per-provider extraction outputs |
| S3 analysis | `.../analysis/cross-llm/.../*.json` | Cross-provider comparison |
| GitHub | `daily/YYYY-MM-DD/*.{md,json}` | Daily feed files |
| GitHub | `daily/latest/*.{md,json}` | Overwritten each run |
| GitHub | `indexes/latest.json` | Hot index |
| Supabase | `llm.learnings` | All learning rows |
| Supabase | `llm.conversations` | Conversation metadata |
| Supabase | `llm.chunks` | Chunk registry |
| Supabase | `llm.daily_digest` | Daily summary rows |
| Supabase | `llm.analysis_jobs` | Job audit row |

---

## Error handling

| Error | Impact | Rollback | Next |
|-------|--------|----------|------|
| S3 read failure | Full abort | Mark job failed | Check bucket policy, retry |
| Chunk write failure | Partial output | Delete partial chunks | Retry from chunk step |
| Bedrock throttle | Slow extraction | Exponential backoff | Reduce batch size |
| Learning schema validation fail | Skip that learning | Log to errors[] | Review extraction prompt |
| Supabase insert fail | Data not queryable | Retry 3x with backoff | Alert Telegram |
| GitHub API fail | Daily feed not written | Retry 3x | Fall back to S3-only output |

All errors appended to `llm.analysis_jobs.errors` JSONB array.  
Job is `partial` if any step fails but others succeed.  
Job is `failed` only if corpus is unreadable or chunking fails entirely.

---

## IAM requirements

```yaml
Actions:
  - s3:GetObject        # t4h-llm-json/*
  - s3:PutObject        # t4h-llm-json/llm-json/chunks|analysis|daily/*
  - s3:HeadObject       # t4h-llm-json/*
  - bedrock:InvokeModel # claude-haiku-* (extraction)
  - ssm:GetParameter    # /t4h/llm-json/* (secrets)
  - secretsmanager:GetSecretValue  # github PAT, supabase service key
```

---

## SAM template (abbreviated)

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31

Resources:
  AnalyseInPlaceFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: llm-json-analyse-in-place
      Handler: handler.lambda_handler
      Runtime: python3.12
      Timeout: 900
      MemorySize: 2048
      Environment:
        Variables:
          S3_BUCKET: t4h-llm-json
          SUPABASE_URL: !Sub '{{resolve:ssm:/t4h/supabase/s1/url}}'
          SUPABASE_SERVICE_KEY: !Sub '{{resolve:ssm:/t4h/supabase/s1/service_key}}'
          GITHUB_TOKEN: !Sub '{{resolve:ssm:/t4h/github/pat_2}}'
          GITHUB_REPO: TML-4PM/LLM-JSON
          TELEGRAM_BOT_TOKEN: !Sub '{{resolve:ssm:/t4h/telegram/bot_token}}'
          TELEGRAM_CHAT_ID: !Sub '{{resolve:ssm:/t4h/telegram/chat_id}}'
          IS_RD: "true"
          PROJECT_CODE: LLM-JSON
      Events:
        S3RawUpload:
          Type: S3
          Properties:
            Bucket: !Ref LlmJsonBucket
            Events: s3:ObjectCreated:*
            Filter:
              S3Key:
                Rules:
                  - Name: prefix
                    Value: llm-json/raw/
                  - Name: suffix
                    Value: .jsonl.gz
      Policies:
        - S3CrudPolicy:
            BucketName: t4h-llm-json
        - Version: '2012-10-17'
          Statement:
            - Effect: Allow
              Action: bedrock:InvokeModel
              Resource: '*'
      Tags:
        is_rd: "true"
        project_code: LLM-JSON
        owner: t4h
```

---

## Extraction prompts used

| Step | Prompt file |
|------|-------------|
| Conversation summary | `prompts/summarisation/conversation.md` |
| Decisions | `prompts/extraction/decisions.md` |
| Tasks | `prompts/extraction/tasks.md` |
| Entities | `prompts/extraction/entities.md` |
| Assets/URLs | `prompts/extraction/assets.md` |
| Cross-LLM summary | `prompts/summarisation/cross_llm.md` |
| Contradiction detection | `prompts/contradiction_detection/cross_provider.md` |
| Reuse pattern ID | `prompts/reuse_patterns/identify_reusable.md` |

---

## Kill switch

```bash
# Disable S3 trigger
aws lambda remove-event-source-mapping \
  --uuid <mapping-uuid> \
  --region ap-southeast-2

# Or toggle via cap_secrets
# Key: LLM_ANALYSE_IN_PLACE_ENABLED = false
```
