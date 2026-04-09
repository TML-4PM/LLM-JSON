# t4h-llm-drive-reader

Lambda that reads JSON conversation exports from a Google Drive folder via service account,
extracts structured signal, and writes to Supabase tables.

## Tables written
- `llm_thread_index` — one row per conversation thread
- `llm_code_blocks` — extracted code snippets
- `llm_extracted_actions` — action items and next steps
- `llm_unfinished_threads` — threads with open/blocked signals
- `llm_topic_weights` — topic frequency per file

## Bridge calls

### List files in folder
```json
{"fn": "t4h-llm-drive-reader", "payload": {"action": "list", "folder_id": "FOLDER_ID"}}
```

### Index entire folder (reads all .json files → Supabase)
```json
{"fn": "t4h-llm-drive-reader", "payload": {"action": "index", "folder_id": "13Jf_l1m-Vk5XKhq4dWR6ySafbIH8yub_"}}
```

### Read single file
```json
{"fn": "t4h-llm-drive-reader", "payload": {"action": "read_file", "file_id": "FILE_ID", "source_llm": "claude"}}
```

### Dry run (extract only, no write)
```json
{"fn": "t4h-llm-drive-reader", "payload": {"action": "index", "folder_id": "...", "dry_run": true}}
```

## Deploy
```bash
cd lambdas/t4h-llm-drive-reader
bash deploy.sh
```

## SA
`gdrive-crawler@mcp-bridge-478002.iam.gserviceaccount.com`  
Key stored in `cap_secrets.GOOGLE_SA_JSON`
