"""
llm-json-analyse-in-place
Lambda handler: stream-read raw corpus from S3, chunk, extract learnings,
write daily feed to GitHub, sync to Supabase.

Trigger: S3 PutObject on llm-json/raw/**/*.jsonl.gz
         OR manual bridge invoke with explicit s3_uri

RDTI: is_rd=true, project_code=LLM-JSON
"""

import os
import json
import gzip
import uuid
import base64
import hashlib
import logging
import datetime
from typing import Optional
from io import BytesIO

import boto3
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ["S3_BUCKET"]
S3_PREFIX = os.environ.get("S3_PREFIX", "llm-json")
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE_MESSAGES", "500"))
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "anthropic.claude-haiku-4-5-20251001")
KILL_SWITCH = os.environ.get("KILL_SWITCH_ENABLED", "false").lower() == "true"

s3 = boto3.client("s3", region_name="ap-southeast-2")
bedrock = boto3.client("bedrock-runtime", region_name="ap-southeast-2")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    if KILL_SWITCH:
        logger.warning("Kill switch enabled — aborting")
        return {"ok": False, "reason": "kill_switch"}

    s3_uri, provider, date_str = resolve_input(event)
    if not s3_uri:
        return {"ok": False, "error": "Could not resolve S3 URI from event"}

    job_id = f"job_{date_str.replace('-','')}_{uuid.uuid4().hex[:8]}"
    logger.info(f"Starting job {job_id} | {provider} | {date_str} | {s3_uri}")

    stats = {
        "job_id": job_id, "s3_uri": s3_uri, "provider": provider, "date": date_str,
        "chunks": 0, "conversations": 0, "learnings": 0, "errors": []
    }

    try:
        # 1. Register job
        register_job(job_id, s3_uri, provider, date_str)

        # 2. Ensure manifest
        ensure_manifest(s3_uri, provider, date_str)

        # 3. Chunk + classify + extract
        chunks = chunk_corpus(s3_uri, provider, date_str, job_id, stats)

        # 4. Write analysis outputs to S3
        write_analysis_outputs(chunks, provider, date_str, stats)

        # 5. Build daily feed → GitHub
        invoke_daily_feed(provider, date_str)

        # 6. Update corpus_registry in Supabase
        update_corpus_registry(s3_uri, provider, date_str, stats)

        # 7. Close job
        close_job(job_id, stats)

        # 8. Notify
        notify(provider, date_str, stats)

        return {"ok": True, "job_id": job_id, "stats": stats}

    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        stats["errors"].append(str(e))
        close_job(job_id, stats, failed=True)
        return {"ok": False, "job_id": job_id, "error": str(e), "stats": stats}


# ─── INPUT RESOLUTION ─────────────────────────────────────────────────────────

def resolve_input(event):
    """Resolve S3 URI, provider, date from either S3 event or manual invoke."""
    # S3 trigger
    if "Records" in event:
        rec = event["Records"][0]["s3"]
        bucket = rec["bucket"]["name"]
        key = rec["object"]["key"]
        s3_uri = f"s3://{bucket}/{key}"
        provider = extract_provider_from_key(key)
        date_str = extract_date_from_key(key)
        return s3_uri, provider, date_str

    # Manual bridge invoke
    payload = event.get("payload", event)
    s3_uri = payload.get("s3_uri")
    provider = payload.get("provider") or (extract_provider_from_key(s3_uri) if s3_uri else None)
    date_str = payload.get("date") or (extract_date_from_key(s3_uri) if s3_uri else None)
    return s3_uri, provider, date_str or datetime.date.today().isoformat()


def extract_provider_from_key(key):
    for p in ["gpt", "claude", "perplexity", "gemini", "grok"]:
        if f"provider={p}" in key:
            return p
    return "unknown"


def extract_date_from_key(key):
    import re
    m = re.search(r"year=(\d{4})/month=(\d{2})/day=(\d{2})", key or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return datetime.date.today().isoformat()


# ─── MANIFEST ─────────────────────────────────────────────────────────────────

def ensure_manifest(s3_uri, provider, date_str):
    bucket, key = parse_s3_uri(s3_uri)
    manifest_key = key.rsplit("/", 1)[0] + "/manifest.json"
    try:
        s3.head_object(Bucket=bucket, Key=manifest_key)
        return  # Already exists
    except s3.exceptions.ClientError:
        pass

    head = s3.head_object(Bucket=bucket, Key=key)
    manifest = {
        "s3_uri": s3_uri,
        "provider": provider,
        "date_collected": date_str,
        "file_size_bytes": head["ContentLength"],
        "last_modified": head["LastModified"].isoformat(),
        "processing_status": "pending",
        "schema_version": "1.0",
        "generated_by": "analyse_in_place",
        "generated_at": utcnow()
    }
    s3.put_object(
        Bucket=bucket, Key=manifest_key,
        Body=json.dumps(manifest, indent=2).encode(),
        ContentType="application/json",
        Tagging="is_rd=true&project_code=LLM-JSON"
    )
    logger.info(f"Manifest written: s3://{bucket}/{manifest_key}")


# ─── CHUNKING ─────────────────────────────────────────────────────────────────

def chunk_corpus(s3_uri, provider, date_str, job_id, stats):
    """Stream-read raw corpus, split into chunks, extract learnings per chunk."""
    bucket, key = parse_s3_uri(s3_uri)
    y, m, d = date_str.split("-")
    chunk_prefix = f"{S3_PREFIX}/chunks/provider={provider}/year={y}/month={m}/day={d}"

    chunks = []
    buffer = []
    chunk_seq = 0
    conversation_count = 0

    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"]

    with gzip.open(body, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            buffer.append(record)
            conversation_count += 1

            if len(buffer) >= CHUNK_SIZE:
                chunk = flush_chunk(bucket, chunk_prefix, buffer, chunk_seq, provider, date_str, stats)
                chunks.append(chunk)
                buffer = []
                chunk_seq += 1

    if buffer:
        chunk = flush_chunk(bucket, chunk_prefix, buffer, chunk_seq, provider, date_str, stats)
        chunks.append(chunk)

    stats["conversations"] = conversation_count
    stats["chunks"] = len(chunks)
    return chunks


def flush_chunk(bucket, prefix, records, seq, provider, date_str, stats):
    chunk_id = f"chunk_{provider}_{date_str.replace('-','')}_{seq:06d}"
    chunk_key = f"{prefix}/chunk-{seq:06d}.jsonl.gz"

    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in records:
            gz.write((json.dumps(r) + "\n").encode())
    buf.seek(0)

    s3.put_object(
        Bucket=bucket, Key=chunk_key,
        Body=buf.read(),
        ContentType="application/x-gzip",
        Tagging=f"is_rd=true&project_code=LLM-JSON&provider={provider}"
    )

    # Register chunk in Supabase
    supabase_upsert("llm.chunks", {
        "chunk_id": chunk_id,
        "provider": provider,
        "s3_uri": f"s3://{bucket}/{chunk_key}",
        "chunk_sequence": seq,
        "message_start": seq * CHUNK_SIZE,
        "message_end": seq * CHUNK_SIZE + len(records) - 1,
        "size_bytes": 0,
        "conversation_ids": []
    })

    # Extract learnings from this chunk
    learnings = extract_learnings_from_chunk(records, provider, date_str, chunk_id, stats)
    return {"chunk_id": chunk_id, "s3_uri": f"s3://{bucket}/{chunk_key}", "learnings": learnings}


# ─── EXTRACTION ───────────────────────────────────────────────────────────────

def extract_learnings_from_chunk(records, provider, date_str, chunk_ref, stats):
    """Call Bedrock to extract learnings from a batch of conversations."""
    learnings = []
    batch_text = summarise_chunk_for_extraction(records, provider)
    if not batch_text:
        return learnings

    prompt = build_extraction_prompt(batch_text, provider, date_str)
    try:
        response = bedrock.invoke_model(
            modelId=EXTRACTION_MODEL,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}]
            }),
            contentType="application/json",
            accept="application/json"
        )
        result = json.loads(response["body"].read())
        raw_text = result["content"][0]["text"]
        extracted = parse_extraction_response(raw_text, provider, date_str, chunk_ref)
        learnings.extend(extracted)
        stats["learnings"] += len(extracted)

        # Batch upsert to Supabase
        for learning in extracted:
            supabase_upsert("llm.learnings", learning)

    except Exception as e:
        logger.warning(f"Extraction failed for chunk {chunk_ref}: {e}")
        stats["errors"].append(f"extraction:{chunk_ref}:{str(e)}")

    return learnings


def summarise_chunk_for_extraction(records, provider):
    """Build a compact text representation of chunk for extraction prompt."""
    lines = []
    for i, rec in enumerate(records[:50]):  # Cap at 50 convos per extraction call
        cid = rec.get("id") or rec.get("conversation_id") or f"conv_{i}"
        msgs = rec.get("messages") or rec.get("mapping") or []
        if isinstance(msgs, dict):
            msgs = list(msgs.values())
        text_parts = []
        for msg in (msgs[:10] if isinstance(msgs, list) else []):
            role = msg.get("role") or msg.get("author", {}).get("role", "")
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            if content and role:
                text_parts.append(f"[{role}]: {str(content)[:200]}")
        if text_parts:
            lines.append(f"CONV {cid}:\n" + "\n".join(text_parts))
    return "\n\n---\n\n".join(lines)


def build_extraction_prompt(chunk_text, provider, date_str):
    return f"""You are extracting structured learnings from LLM conversation exports.
Provider: {provider}. Date: {date_str}.

Extract ALL of the following from these conversations:
- prompt_win: prompts that produced excellent results
- decision: explicit decisions made
- blocker: unresolved problems mentioned
- reusable_pattern: workflows or patterns worth reusing
- memory_candidate: facts that should be remembered long-term
- contradiction: conflicting information
- opportunity: business or technical opportunities identified

Return a JSON array of learning objects. Each must have:
- learning_id: "lrn_{date_str.replace('-','')}_{{}}" (use 6-digit counter)
- learning_type: one of the types above
- title: short title (max 80 chars)
- summary: 1-3 sentences, plain text
- reusability: high|medium|low|none
- confidence: 0.0-1.0
- tags: array of lowercase strings
- feed_forward: {{"should_update_prompt_pack": bool, "should_update_memory": bool, "should_create_task": bool}}

Return ONLY valid JSON array. No markdown. No explanation.

CONVERSATIONS:
{chunk_text[:8000]}"""


def parse_extraction_response(raw_text, provider, date_str, chunk_ref):
    """Parse Bedrock response into learning objects."""
    learnings = []
    try:
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        items = json.loads(text)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item.setdefault("schema_version", "1.0")
            item.setdefault("date", date_str)
            item.setdefault("source_provider", provider)
            item.setdefault("chunk_ref", chunk_ref)
            item.setdefault("evidence", [{"type": "chunk_ref", "locator": chunk_ref}])
            item.setdefault("extracted_by", "llm-json-analyse-in-place")
            item.setdefault("created_at", utcnow())
            item["is_rd"] = True
            item["project_code"] = "LLM-JSON"
            if not item.get("learning_id"):
                item["learning_id"] = f"lrn_{date_str.replace('-','')}_{i:06d}"
            learnings.append(item)
    except Exception as e:
        logger.warning(f"Failed to parse extraction response: {e}")
    return learnings


# ─── ANALYSIS OUTPUTS ─────────────────────────────────────────────────────────

def write_analysis_outputs(chunks, provider, date_str, stats):
    y, m, d = date_str.split("-")
    prefix = f"{S3_PREFIX}/analysis/provider={provider}/year={y}/month={m}/day={d}"

    all_learnings = [l for chunk in chunks for l in chunk["learnings"]]
    summary = {
        "provider": provider,
        "date": date_str,
        "chunk_count": len(chunks),
        "learning_count": len(all_learnings),
        "by_type": {},
        "generated_at": utcnow()
    }
    for l in all_learnings:
        t = l.get("learning_type", "unknown")
        summary["by_type"][t] = summary["by_type"].get(t, 0) + 1

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"{prefix}/daily_stats.json",
        Body=json.dumps(summary, indent=2).encode(),
        ContentType="application/json",
        Tagging=f"is_rd=true&project_code=LLM-JSON&provider={provider}"
    )

    if all_learnings:
        buf = BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for l in all_learnings:
                gz.write((json.dumps(l) + "\n").encode())
        buf.seek(0)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"{prefix}/learnings.ndjson.gz",
            Body=buf.read(),
            ContentType="application/x-gzip",
            Tagging=f"is_rd=true&project_code=LLM-JSON&provider={provider}"
        )


# ─── DAILY FEED INVOKE ────────────────────────────────────────────────────────

def invoke_daily_feed(provider, date_str):
    """Invoke build_daily_feed Lambda asynchronously."""
    lam = boto3.client("lambda", region_name="ap-southeast-2")
    try:
        lam.invoke(
            FunctionName="llm-json-build-daily-feed",
            InvocationType="Event",
            Payload=json.dumps({"date": date_str, "provider": provider, "source": "analyse_in_place"}).encode()
        )
    except Exception as e:
        logger.warning(f"Failed to invoke daily feed Lambda: {e}")


# ─── SUPABASE ─────────────────────────────────────────────────────────────────

def supabase_upsert(table, data):
    schema, tbl = table.split(".")
    url = f"{SUPABASE_URL}/rest/v1/{tbl}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
            "Accept-Profile": schema,
            "Content-Profile": schema
        }
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        logger.warning(f"Supabase upsert {table} failed: {e.code} {e.read()[:200]}")


def register_job(job_id, s3_uri, provider, date_str):
    supabase_upsert("llm.analysis_jobs", {
        "job_id": job_id,
        "job_type": "analyse_in_place",
        "status": "running",
        "started_at": utcnow(),
        "notes": f"provider={provider} date={date_str} uri={s3_uri}"
    })


def close_job(job_id, stats, failed=False):
    supabase_upsert("llm.analysis_jobs", {
        "job_id": job_id,
        "status": "failed" if failed else ("partial" if stats["errors"] else "complete"),
        "completed_at": utcnow(),
        "chunks_processed": stats.get("chunks", 0),
        "learnings_extracted": stats.get("learnings", 0),
        "errors": stats.get("errors", [])
    })


def update_corpus_registry(s3_uri, provider, date_str, stats):
    corpus_id = f"corpus_{provider}_{date_str.replace('-','')}_{stats['job_id'][-8:]}"
    supabase_upsert("llm.corpus_registry", {
        "corpus_id": corpus_id,
        "provider": provider,
        "s3_raw_uri": s3_uri,
        "date_collected": date_str,
        "date_processed": date_str,
        "processing_status": "complete" if not stats["errors"] else "partial",
        "chunk_count": stats["chunks"],
        "conversation_count": stats["conversations"],
        "is_rd": True,
        "project_code": "LLM-JSON"
    })


# ─── NOTIFY ───────────────────────────────────────────────────────────────────

def notify(provider, date_str, stats):
    msg = (
        f"✅ LLM-JSON analyse-in-place complete\n"
        f"Provider: {provider} | Date: {date_str}\n"
        f"Chunks: {stats['chunks']} | Convos: {stats['conversations']} | Learnings: {stats['learnings']}\n"
        f"Errors: {len(stats['errors'])}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.warning(f"Telegram notify failed: {e}")


# ─── UTILS ────────────────────────────────────────────────────────────────────

def parse_s3_uri(uri):
    uri = uri.replace("s3://", "")
    bucket, key = uri.split("/", 1)
    return bucket, key


def utcnow():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
