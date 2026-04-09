"""
t4h-llm-drive-reader
Reads JSON conversation files from a Drive folder using SA creds.
Extracts threads, topics, code blocks, action items, unfinished threads.
Writes results to Supabase llm_* tables.
Callable via bridge: {"fn": "t4h-llm-drive-reader", "payload": {"folder_id": "...", "action": "index|read_file|status"}}
"""
import json
import os
import re
import hashlib
import traceback
from collections import Counter
from io import BytesIO

import boto3
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]          # https://lzfgigiyqpuuxslsygjt.supabase.co
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service_role key
SECRETS_TABLE = "cap_secrets"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

TOPIC_HINTS = [
    "ai","automation","agent","agents","supabase","bridge","lambda","sql",
    "governance","portfolio","wave 10","wave10","architecture","mcp","stripe",
    "outcome ready","reading buddy","workfamilyai","holoorg","neuropak",
    "consentx","myneuralsignal","valdocco","bci","calendar","email","chrome",
    "aws","notion","n8n","rdti","bas","div7a","revenue","supabase","vercel",
    "llm","gpt","claude","gemini","grok","openai","anthropic","cursor",
    "command centre","ccq","rip","snap","maat","fire","orchestrator",
    "tradie","aquame","smartpark","medledger","apac","holo","ennead",
    "reading","buddy","decision","blocker","next step","todo"
]
CODE_RE = re.compile(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)
ACTION_RE = re.compile(
    r"(?:^|\n)\s*(?:[-*•]\s+)?(?:TODO|Action|Next step|Next steps|Open item|NEXT|DO THIS)[:\-]?\s*(.+)",
    re.I
)
UNFINISHED_SIGNALS = [
    "next step","next steps","todo","to do","unfinished","open item",
    "blocked","needs doing","to finish","not complete","missing",
    "follow up","follow-up","wip","in progress","partial"
]

# ── SA helper ─────────────────────────────────────────────────────────────────
def get_sa_creds():
    """Pull GOOGLE_SA_JSON from cap_secrets via Supabase REST."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{SECRETS_TABLE}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        params={"key": "eq.GOOGLE_SA_JSON", "select": "value"},
        timeout=10
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError("GOOGLE_SA_JSON not found in cap_secrets")
    sa_json = rows[0]["value"]
    if isinstance(sa_json, str):
        sa_json = json.loads(sa_json)
    return service_account.Credentials.from_service_account_info(sa_json, scopes=SCOPES)


def get_drive_service():
    creds = get_sa_creds()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ── Text extraction ────────────────────────────────────────────────────────────
def extract_text(obj, depth=0):
    if depth > 10:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(extract_text(x, depth+1) for x in obj)
    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            if k.lower() in {"text","content","message","body","prompt","completion","title","summary","parts","value"}:
                parts.append(extract_text(v, depth+1))
            else:
                parts.append(extract_text(v, depth+1))
        return "\n".join(p for p in parts if p.strip())
    return ""


def detect_topics(text):
    lower = text.lower()
    return [t for t in TOPIC_HINTS if t in lower]


def detect_actions(text):
    actions = []
    for m in ACTION_RE.finditer(text):
        a = m.group(1).strip()
        if len(a) > 10:
            actions.append(a[:500])
    return actions[:50]


def is_unfinished(text):
    lower = text.lower()
    return any(s in lower for s in UNFINISHED_SIGNALS)


def infer_title(thread, idx):
    if isinstance(thread, dict):
        for k in ("title","name","subject","conversation_title","chat_title"):
            if thread.get(k):
                return str(thread[k])[:200]
    return f"thread_{idx+1}"


def infer_thread_id(thread, idx):
    if isinstance(thread, dict):
        for k in ("id","uuid","conversation_id","thread_id"):
            if thread.get(k):
                return str(thread[k])
    return f"auto_{idx+1}"


# ── Parse conversation JSON ────────────────────────────────────────────────────
def parse_conversations(data, source_label):
    """
    Handle multiple export shapes:
      - list of threads
      - {"conversations": [...]}
      - {"messages": [...]}  (single thread)
      - GPT / Gemini / Grok shapes
    Returns list of thread dicts.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("conversations","chats","threads","items","data"):
            if k in data and isinstance(data[k], list):
                return data[k]
        # single thread wrapped in dict
        if "messages" in data or "mapping" in data:
            return [data]
    return []


def process_file(file_id, file_name, source_llm, drive_service):
    """Download file from Drive, parse, extract signal."""
    request = drive_service.files().get_media(fileId=file_id)
    buf = BytesIO()
    dl = MediaIoBaseDownload(buf, request, chunksize=50*1024*1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    raw = buf.getvalue()

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"error": f"JSON parse failed for {file_name}", "rows": 0}

    threads = parse_conversations(data, source_llm)
    topic_counter = Counter()
    thread_rows, code_rows, action_rows, unfinished_rows = [], [], [], []

    for i, thread in enumerate(threads):
        title = infer_title(thread, i)
        tid = infer_thread_id(thread, i)
        text = extract_text(thread)
        topics = detect_topics(text)
        for t in topics:
            topic_counter[t] += 1
        actions = detect_actions(text)
        codes = CODE_RE.findall(text)
        unfinished = is_unfinished(text)

        thread_rows.append({
            "thread_id": tid,
            "title": title,
            "source_llm": source_llm,
            "source_file": file_name,
            "message_count": max(1, text.count("\n\n")),
            "has_code": bool(codes),
            "action_count": len(actions),
            "unfinished_signal": unfinished,
            "topics": topics[:20],
            "content_hash": hashlib.sha256(text[:10000].encode()).hexdigest()
        })
        for lang, code in codes[:20]:
            code_rows.append({
                "thread_id": tid,
                "title": title,
                "source_llm": source_llm,
                "language": lang or "unknown",
                "code_snippet": code[:5000],
                "source_file": file_name
            })
        for a in actions:
            action_rows.append({
                "thread_id": tid,
                "title": title,
                "source_llm": source_llm,
                "action_text": a,
                "status": "open",
                "source_file": file_name
            })
        if unfinished:
            unfinished_rows.append({
                "thread_id": tid,
                "title": title,
                "source_llm": source_llm,
                "status": "unfinished",
                "source_file": file_name
            })

    topic_rows = [{"topic": t, "mention_count": c, "source_llm": source_llm, "source_file": file_name}
                  for t, c in topic_counter.most_common(50)]

    return {
        "threads": thread_rows,
        "code": code_rows,
        "actions": action_rows,
        "unfinished": unfinished_rows,
        "topics": topic_rows,
        "stats": {
            "thread_count": len(thread_rows),
            "code_count": len(code_rows),
            "action_count": len(action_rows),
            "unfinished_count": len(unfinished_rows),
            "topic_count": len(topic_rows)
        }
    }


# ── Supabase write ─────────────────────────────────────────────────────────────
def sb_upsert(table, rows, on_conflict=None):
    if not rows:
        return 0
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    if on_conflict:
        headers["Prefer"] += f",resolution=merge-duplicates"
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"

    # Batch 200 at a time
    inserted = 0
    for i in range(0, len(rows), 200):
        batch = rows[i:i+200]
        resp = requests.post(url, headers=headers, json=batch, timeout=30)
        if resp.status_code not in (200, 201):
            print(f"  WARN {table} batch {i}: {resp.status_code} {resp.text[:200]}")
        else:
            inserted += len(batch)
    return inserted


# ── Ensure tables ──────────────────────────────────────────────────────────────
ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS public.llm_thread_index (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  thread_id text NOT NULL,
  title text,
  source_llm text,
  source_file text,
  message_count int DEFAULT 0,
  has_code bool DEFAULT false,
  action_count int DEFAULT 0,
  unfinished_signal bool DEFAULT false,
  topics text[],
  content_hash text,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(content_hash)
);
CREATE TABLE IF NOT EXISTS public.llm_code_blocks (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  thread_id text,
  title text,
  source_llm text,
  source_file text,
  language text,
  code_snippet text,
  created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.llm_extracted_actions (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  thread_id text,
  title text,
  source_llm text,
  source_file text,
  action_text text,
  status text DEFAULT 'open',
  created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.llm_unfinished_threads (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  thread_id text,
  title text,
  source_llm text,
  source_file text,
  status text DEFAULT 'unfinished',
  created_at timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.llm_topic_weights (
  id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  topic text,
  source_llm text,
  source_file text,
  mention_count int DEFAULT 0,
  created_at timestamptz DEFAULT now()
);
"""


def ensure_tables():
    """Run DDL via Supabase SQL endpoint (service role only)."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "application/json"},
        json={"query": ENSURE_SQL},
        timeout=30
    )
    # If exec_sql RPC doesn't exist we skip silently — tables should exist
    return resp.status_code


# ── Lambda handler ─────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    action = event.get("action", "index")
    folder_id = event.get("folder_id", "13Jf_l1m-Vk5XKhq4dWR6ySafbIH8yub_")  # LLM-JSON-Intake default
    file_id = event.get("file_id")
    source_llm = event.get("source_llm", "unknown")
    dry_run = event.get("dry_run", False)

    try:
        drive_service = get_drive_service()

        # ── LIST ──────────────────────────────────────────────────────────────
        if action == "list":
            results = drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id,name,size,mimeType,modifiedTime)",
                pageSize=100
            ).execute()
            files = results.get("files", [])
            return {"status": "ok", "folder_id": folder_id, "file_count": len(files), "files": files}

        # ── INDEX (list + process all JSON files in folder) ───────────────────
        if action == "index":
            results = drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id,name,size,mimeType,modifiedTime)",
                pageSize=100
            ).execute()
            files = [f for f in results.get("files", [])
                     if f.get("name", "").endswith(".json") or "json" in f.get("mimeType","")]

            ensure_tables()
            total_stats = {"threads":0,"code":0,"actions":0,"unfinished":0,"topics":0}
            processed = []
            errors = []

            for f in files:
                fname = f["name"]
                fid = f["id"]
                # Infer LLM from filename
                llm = source_llm
                lower = fname.lower()
                for candidate in ["claude","gpt","gemini","grok","mixed"]:
                    if candidate in lower:
                        llm = candidate
                        break

                try:
                    extracted = process_file(fid, fname, llm, drive_service)
                    if "error" in extracted:
                        errors.append({"file": fname, "error": extracted["error"]})
                        continue

                    if not dry_run:
                        sb_upsert("llm_thread_index", extracted["threads"], on_conflict="content_hash")
                        sb_upsert("llm_code_blocks", extracted["code"])
                        sb_upsert("llm_extracted_actions", extracted["actions"])
                        sb_upsert("llm_unfinished_threads", extracted["unfinished"])
                        sb_upsert("llm_topic_weights", extracted["topics"])

                    stats = extracted["stats"]
                    for k in total_stats:
                        total_stats[k] += stats.get(f"{k}_count", 0)
                    processed.append({"file": fname, "llm": llm, **stats})

                except Exception as e:
                    errors.append({"file": fname, "error": str(e), "trace": traceback.format_exc()[-500:]})

            return {
                "status": "ok",
                "dry_run": dry_run,
                "files_processed": len(processed),
                "files_errored": len(errors),
                "totals": total_stats,
                "processed": processed,
                "errors": errors
            }

        # ── READ_FILE (single file by id) ─────────────────────────────────────
        if action == "read_file":
            if not file_id:
                return {"status": "error", "error": "file_id required"}
            meta = drive_service.files().get(fileId=file_id, fields="id,name,size,mimeType").execute()
            extracted = process_file(file_id, meta["name"], source_llm, drive_service)
            if not dry_run and "error" not in extracted:
                sb_upsert("llm_thread_index", extracted["threads"], on_conflict="content_hash")
                sb_upsert("llm_code_blocks", extracted["code"])
                sb_upsert("llm_extracted_actions", extracted["actions"])
                sb_upsert("llm_unfinished_threads", extracted["unfinished"])
                sb_upsert("llm_topic_weights", extracted["topics"])
            return {"status": "ok", "file": meta["name"], **extracted.get("stats", {})}

        return {"status": "error", "error": f"unknown action: {action}"}

    except Exception as e:
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()[-1000:]}
