# S3 Storage Contract — t4h-llm-json

**Bucket:** `t4h-llm-json`  
**Region:** `ap-southeast-2`  
**Account:** `140548542136`  
**Versioning:** Enabled  
**Lifecycle rules:** Raw files → Glacier after 90 days, delete after 2 years  

---

## Canonical path patterns

### Raw exports
```
llm-json/raw/provider={provider}/year={YYYY}/month={MM}/day={DD}/{filename}.jsonl.gz
llm-json/raw/provider={provider}/year={YYYY}/month={MM}/day={DD}/manifest.json
```

**Rules:**
- `{provider}` must be one of: `gpt`, `claude`, `perplexity`, `gemini`, `grok`
- `{YYYY}`, `{MM}`, `{DD}` are zero-padded: `2026`, `04`, `10`
- Raw files always `.jsonl.gz` — never plain `.json`, never `.zip`
- `manifest.json` written alongside every raw file (created by upload script or Lambda)
- Multiple files per day allowed — increment with timestamp suffix: `export_143022.jsonl.gz`

### Chunks
```
llm-json/chunks/provider={provider}/year={YYYY}/month={MM}/day={DD}/chunk-{NNNNNN}.jsonl.gz
```

**Rules:**
- `{NNNNNN}` is zero-padded 6-digit integer: `000001`, `000002`
- Target chunk size: ~500 messages or ~50MB compressed, whichever is smaller
- Chunks are immutable once written — never overwrite, create new corpus instead
- Chunks reference back to their source via manifest

### Analysis outputs
```
llm-json/analysis/provider={provider}/year={YYYY}/month={MM}/day={DD}/{output_type}.json
llm-json/analysis/cross-llm/year={YYYY}/month={MM}/day={DD}/{output_type}.json
```

**Output types:** `learnings.ndjson`, `conversations_summary.json`, `entities.json`, `contradictions.json`, `daily_stats.json`

### Daily aggregates (S3 copy of GitHub daily/)
```
llm-json/daily/year={YYYY}/month={MM}/day={DD}/{filename}
```

### Registries (always latest, overwritten on each run)
```
llm-json/registries/corpus_registry.json
llm-json/registries/analysis_registry.json
llm-json/registries/provider_profiles.json
```

---

## Object tagging (required on all writes)

```
is_rd = true
project_code = LLM-JSON
owner = t4h
provider = {gpt|claude|perplexity|gemini|grok}
```

---

## Access control

| Principal | Access | Scope |
|-----------|--------|-------|
| Lambda `llm-json-analyse-in-place` | GetObject, PutObject, HeadObject | `llm-json/raw/*`, `llm-json/chunks/*`, `llm-json/analysis/*`, `llm-json/daily/*`, `llm-json/registries/*` |
| Lambda `llm-json-build-daily-feed` | GetObject, PutObject | `llm-json/analysis/*`, `llm-json/daily/*`, `llm-json/registries/*` |
| Troy (console) | Full | All |
| All other Lambdas | None | None |

No public access. No pre-signed URL generation except from designated API endpoint.

---

## What does NOT go in S3

- Raw Python scripts or Lambda code (→ GitHub)
- Manifests and schemas (→ GitHub)
- Daily feed files primary copy (→ GitHub, S3 is secondary copy)
- Supabase dumps (→ S3 separate backup bucket, not this one)
- Git repos or node_modules

---

## Lifecycle rules

| Prefix | Transition | Action |
|--------|-----------|--------|
| `llm-json/raw/` | 90 days | Move to Glacier Instant Retrieval |
| `llm-json/raw/` | 730 days | Delete |
| `llm-json/chunks/` | 180 days | Move to Glacier Instant Retrieval |
| `llm-json/chunks/` | 730 days | Delete |
| `llm-json/analysis/` | 365 days | Delete |
| `llm-json/daily/` | 365 days | Delete |
| `llm-json/registries/` | Never | Keep latest, versioning covers history |

---

## Naming convention for uploaded files

Use this pattern when exporting from provider interfaces:

```
{provider}_export_{YYYYMMDD}_{HHMMSS}.jsonl.gz
```

Examples:
```
gpt_export_20260410_143022.jsonl.gz
claude_export_20260410_091500.jsonl.gz
perplexity_export_20260410_201145.jsonl.gz
```

---

## Upload script (minimal)

```bash
#!/bin/bash
# upload_corpus.sh
# Usage: ./upload_corpus.sh <provider> <local_file>

PROVIDER=$1
LOCAL_FILE=$2
DATE=$(date +%Y-%m-%d)
YEAR=$(date +%Y)
MONTH=$(date +%m)
DAY=$(date +%d)
TIMESTAMP=$(date +%H%M%S)
BUCKET=t4h-llm-json
KEY="llm-json/raw/provider=${PROVIDER}/year=${YEAR}/month=${MONTH}/day=${DAY}/${PROVIDER}_export_${YEAR}${MONTH}${DAY}_${TIMESTAMP}.jsonl.gz"

# Compress if not already gzipped
if [[ "$LOCAL_FILE" != *.gz ]]; then
  gzip -k "$LOCAL_FILE"
  LOCAL_FILE="${LOCAL_FILE}.gz"
fi

aws s3 cp "$LOCAL_FILE" "s3://${BUCKET}/${KEY}" \
  --region ap-southeast-2 \
  --tagging "is_rd=true&project_code=LLM-JSON&owner=t4h&provider=${PROVIDER}"

echo "Uploaded: s3://${BUCKET}/${KEY}"
echo "S3 event will trigger analyse_in_place Lambda automatically."
```
