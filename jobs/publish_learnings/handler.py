"""
llm-json-publish-learnings
Weekly sweep: promote high-confidence learnings to prompt packs,
queue memory candidates via Telegram, write provider profiles.

Trigger: EventBridge weekly Sunday 03:00 AEST
         OR manual bridge invoke

RDTI: is_rd=true, project_code=LLM-JSON
"""

import os
import json
import base64
import logging
import datetime
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MEMORY_THRESHOLD = float(os.environ.get("MEMORY_CANDIDATE_THRESHOLD", "0.85"))
PROMPT_THRESHOLD = float(os.environ.get("PROMPT_PACK_THRESHOLD", "0.80"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
KILL_SWITCH = os.environ.get("KILL_SWITCH_ENABLED", "false").lower() == "true"


def lambda_handler(event, context):
    if KILL_SWITCH:
        return {"ok": False, "reason": "kill_switch"}

    payload = event.get("payload", event)
    lookback = payload.get("lookback_days", LOOKBACK_DAYS)
    since = (datetime.date.today() - datetime.timedelta(days=lookback)).isoformat()

    logger.info(f"Publishing learnings since {since}")
    stats = {"promoted_prompts": 0, "memory_queued": 0, "profiles_updated": 0, "errors": []}

    try:
        # 1. Promote prompt_wins to prompts/ folder in GitHub
        promote_prompt_wins(since, stats)

        # 2. Send memory candidates to Telegram for Troy review
        queue_memory_candidates(since, stats)

        # 3. Write provider profiles to manifests/providers/
        update_provider_profiles(since, stats)

        # 4. Archive old low-confidence learnings (>90 days, confidence<0.5)
        archive_stale_learnings(stats)

        # 5. Notify summary
        notify(stats, since)

        return {"ok": True, "stats": stats, "since": since}

    except Exception as e:
        logger.exception(f"Publish learnings failed: {e}")
        return {"ok": False, "error": str(e), "stats": stats}


# ─── PROMPT PACK PROMOTION ────────────────────────────────────────────────────

def promote_prompt_wins(since, stats):
    url = (f"{SUPABASE_URL}/rest/v1/learnings"
           f"?learning_type=eq.prompt_win"
           f"&confidence=gte.{PROMPT_THRESHOLD}"
           f"&promoted_to_prompt=eq.false"
           f"&archived=eq.false"
           f"&date=gte.{since}"
           f"&select=*")
    learnings = supabase_get(url)
    logger.info(f"Promoting {len(learnings)} prompt wins to prompt packs")

    for l in learnings:
        slug = slugify(l.get("title", l["learning_id"]))
        tags = l.get("tags", [])
        folder = tags[0] if tags else "general"
        path = f"prompts/{folder}/{slug}.md"
        content = build_prompt_md(l)
        github_put(path, content, f"feat: add prompt pattern — {l.get('title','?')}")

        # Mark promoted in Supabase
        supabase_patch(
            f"{SUPABASE_URL}/rest/v1/learnings?learning_id=eq.{l['learning_id']}",
            {"promoted_to_prompt": True}
        )
        stats["promoted_prompts"] += 1


def build_prompt_md(learning):
    return f"""# {learning.get('title', 'Untitled')}

**Type:** {learning.get('learning_type', '?')}  
**Provider:** {learning.get('source_provider', '?')}  
**Date:** {learning.get('date', '?')}  
**Confidence:** {learning.get('confidence', 0):.2f}  
**Reusability:** {learning.get('reusability', '?')}  
**Tags:** {', '.join(learning.get('tags', []))}

## Summary

{learning.get('summary', '')}

## Usage

Apply this pattern when: {learning.get('summary', '').split('.')[0]}.

## Evidence

Learning ID: `{learning.get('learning_id', '?')}`  
Source: `{learning.get('chunk_ref', learning.get('source_provider', '?'))}`

---
*Auto-promoted by llm-json-publish-learnings on {utcnow()}*
"""


# ─── MEMORY CANDIDATE QUEUE ───────────────────────────────────────────────────

def queue_memory_candidates(since, stats):
    url = (f"{SUPABASE_URL}/rest/v1/learnings"
           f"?learning_type=eq.memory_candidate"
           f"&confidence=gte.{MEMORY_THRESHOLD}"
           f"&promoted_to_memory=eq.false"
           f"&archived=eq.false"
           f"&date=gte.{since}"
           f"&select=learning_id,title,summary,confidence,source_provider,date")
    candidates = supabase_get(url)

    if not candidates:
        return

    lines = [f"🧠 Memory candidates for review ({len(candidates)}):\n"]
    for c in candidates[:10]:
        lines.append(f"• [{c.get('confidence',0):.2f}] {c.get('title','?')}\n  {c.get('summary','')[:120]}")
    lines.append("\nApprove → add to standing memory. Reject → archive.")

    send_telegram("\n".join(lines))
    stats["memory_queued"] = len(candidates)


# ─── PROVIDER PROFILES ────────────────────────────────────────────────────────

def update_provider_profiles(since, stats):
    for provider in ["gpt", "claude", "perplexity", "gemini", "grok"]:
        url = (f"{SUPABASE_URL}/rest/v1/learnings"
               f"?source_provider=eq.{provider}"
               f"&date=gte.{since}"
               f"&select=learning_type,confidence,tags,title")
        learnings = supabase_get(url)
        if not learnings:
            continue

        strengths = [l["title"] for l in learnings if l.get("learning_type") == "model_strength"]
        weaknesses = [l["title"] for l in learnings if l.get("learning_type") == "model_weakness"]
        wins = [l for l in learnings if l.get("learning_type") == "prompt_win"]
        fails = [l for l in learnings if l.get("learning_type") in ("prompt_fail", "anti_pattern")]
        avg_conf = sum(l.get("confidence", 0) for l in learnings) / len(learnings) if learnings else 0

        profile = {
            "provider": provider,
            "period_start": since,
            "period_end": datetime.date.today().isoformat(),
            "total_learnings": len(learnings),
            "prompt_wins": len(wins),
            "prompt_fails": len(fails),
            "avg_confidence": round(avg_conf, 3),
            "strengths": strengths[:5],
            "weaknesses": weaknesses[:3],
            "recommended_use": infer_recommended_use(strengths, weaknesses),
            "updated_at": utcnow()
        }

        github_put(
            f"manifests/providers/{provider}.json",
            json.dumps(profile, indent=2),
            f"chore: update {provider} provider profile"
        )
        stats["profiles_updated"] += 1


def infer_recommended_use(strengths, weaknesses):
    if not strengths:
        return "general"
    text = " ".join(strengths).lower()
    if "code" in text or "sql" in text:
        return "code generation and technical tasks"
    if "summar" in text:
        return "summarisation and extraction"
    if "reason" in text or "analysis" in text:
        return "analysis and reasoning"
    return "general purpose"


# ─── ARCHIVE STALE ────────────────────────────────────────────────────────────

def archive_stale_learnings(stats):
    cutoff = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    url = (f"{SUPABASE_URL}/rest/v1/learnings"
           f"?date=lt.{cutoff}"
           f"&confidence=lt.0.5"
           f"&archived=eq.false"
           f"&select=learning_id")
    stale = supabase_get(url)
    if stale:
        ids = [l["learning_id"] for l in stale]
        logger.info(f"Archiving {len(ids)} stale learnings")
        # Archive in batches of 20
        for i in range(0, len(ids), 20):
            batch = ids[i:i+20]
            id_list = ",".join(batch)
            supabase_patch(
                f"{SUPABASE_URL}/rest/v1/learnings?learning_id=in.({id_list})",
                {"archived": True}
            )


# ─── GITHUB ───────────────────────────────────────────────────────────────────

def github_put(path, content, message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    req_get = urllib.request.Request(url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"})
    sha = None
    try:
        with urllib.request.urlopen(req_get, timeout=10) as r:
            sha = json.loads(r.read())["sha"]
    except Exception:
        pass

    body = {"message": message, "content": base64.b64encode(content.encode()).decode()}
    if sha:
        body["sha"] = sha

    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="PUT",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        logger.warning(f"GitHub PUT failed {path}: {e.code}")


# ─── SUPABASE ─────────────────────────────────────────────────────────────────

def supabase_get(url):
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Accept-Profile": "llm"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.warning(f"Supabase GET failed: {e}")
        return []


def supabase_patch(url, data):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="PATCH",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Content-Profile": "llm",
            "Accept-Profile": "llm",
            "Prefer": "return=minimal"
        }
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        logger.warning(f"Supabase PATCH failed: {e.code}")


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode(),
                                  headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def notify(stats, since):
    msg = (f"📚 LLM-JSON publish-learnings complete\n"
           f"Since: {since}\n"
           f"Prompt packs promoted: {stats['promoted_prompts']}\n"
           f"Memory candidates queued: {stats['memory_queued']}\n"
           f"Provider profiles updated: {stats['profiles_updated']}")
    send_telegram(msg)


# ─── UTILS ────────────────────────────────────────────────────────────────────

def utcnow():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def slugify(text):
    import re
    return re.sub(r"[^a-z0-9-]", "-", text.lower().strip())[:60].strip("-")
