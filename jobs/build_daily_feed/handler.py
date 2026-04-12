"""
llm-json-build-daily-feed
Aggregate learnings for a given date, write all daily/ files to GitHub,
update indexes/latest.json, upsert llm.daily_digest to Supabase.

Trigger: EventBridge daily 02:00 AEST
         OR invoke from analyse_in_place on completion

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
TOP_N_ACTIONS = int(os.environ.get("TOP_N_ACTIONS", "10"))
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE_THRESHOLD", "0.70"))
KILL_SWITCH = os.environ.get("KILL_SWITCH_ENABLED", "false").lower() == "true"

PROVIDERS = ["gpt", "claude", "perplexity", "gemini", "grok"]


def lambda_handler(event, context):
    if KILL_SWITCH:
        return {"ok": False, "reason": "kill_switch"}

    payload = event.get("payload", event)
    date_str = payload.get("date") or datetime.date.today().isoformat()
    run_id = f"daily_{date_str.replace('-','')}_{utcnow_compact()}"

    logger.info(f"Building daily feed for {date_str} | run_id={run_id}")

    try:
        # 1. Pull learnings for date from Supabase
        learnings = fetch_learnings(date_str)
        logger.info(f"Fetched {len(learnings)} learnings for {date_str}")

        # 2. Build all daily output files
        daily = build_daily_outputs(learnings, date_str, run_id)

        # 3. Write to GitHub daily/YYYY-MM-DD/
        write_to_github(daily, date_str, run_id)

        # 4. Update daily/latest/ in GitHub
        write_latest_to_github(daily)

        # 5. Update indexes/latest.json
        update_index(date_str, run_id, len(learnings))

        # 6. Upsert llm.daily_digest
        upsert_daily_digest(daily, date_str, run_id)

        # 7. Notify
        top_action = daily["next_best_actions"][0]["action"] if daily["next_best_actions"] else "none"
        notify(date_str, len(learnings), len(daily["contradictions"]), top_action)

        return {"ok": True, "run_id": run_id, "date": date_str, "learning_count": len(learnings)}

    except Exception as e:
        logger.exception(f"Daily feed build failed: {e}")
        return {"ok": False, "error": str(e), "date": date_str}


# ─── FETCH LEARNINGS ──────────────────────────────────────────────────────────

def fetch_learnings(date_str):
    url = f"{SUPABASE_URL}/rest/v1/learnings?date=eq.{date_str}&archived=eq.false&select=*"
    return supabase_get(url, schema="llm")


# ─── BUILD OUTPUTS ────────────────────────────────────────────────────────────

def build_daily_outputs(learnings, date_str, run_id):
    providers_seen = list({l["source_provider"] for l in learnings if l.get("source_provider")})

    prompt_wins = [l for l in learnings if l.get("learning_type") == "prompt_win"
                   and l.get("confidence", 0) >= MIN_CONFIDENCE]
    failures = [l for l in learnings if l.get("learning_type") in ("prompt_fail", "workflow_fail", "anti_pattern")]
    contradictions = [l for l in learnings if l.get("learning_type") == "contradiction"]
    decisions = [l for l in learnings if l.get("learning_type") in ("decision", "commitment")]
    tasks = [l for l in learnings if l.get("feed_forward", {}).get("should_create_task")]
    memory_candidates = [l for l in learnings if l.get("learning_type") == "memory_candidate"]
    reusable = [l for l in learnings if l.get("reusability") == "high"
                and l.get("learning_type") not in ("prompt_win",)]
    opportunities = [l for l in learnings if l.get("learning_type") == "opportunity"]

    # Build next best actions from blockers + opportunities + tasks
    nba = build_next_best_actions(learnings, TOP_N_ACTIONS)

    # Provider deltas
    provider_deltas = {}
    for p in PROVIDERS:
        p_learnings = [l for l in learnings if l.get("source_provider") == p]
        if p_learnings:
            provider_deltas[p] = build_provider_delta(p, p_learnings, date_str)

    # Cross-LLM summary markdown
    summary_md = build_cross_llm_summary(learnings, date_str, providers_seen, contradictions, nba)

    # Executive brief markdown
    exec_brief_md = build_executive_brief(decisions, tasks, learnings, date_str)

    index = {
        "date": date_str,
        "run_id": run_id,
        "providers_included": providers_seen,
        "total_conversations": len({l.get("conversation_id") for l in learnings if l.get("conversation_id")}),
        "total_learnings": len(learnings),
        "file_manifest": {
            "_index.json": True,
            "cross_llm_summary.md": True,
            "executive_brief.md": True,
            "reusable_patterns.json": bool(reusable),
            "prompt_wins.json": bool(prompt_wins),
            "failure_patterns.json": bool(failures),
            "contradictions.json": bool(contradictions),
            "tasks_created.json": bool(tasks),
            "decisions_detected.json": bool(decisions),
            "memory_candidates.json": bool(memory_candidates),
            "next_best_actions.json": bool(nba),
        },
        "generated_at": utcnow()
    }

    return {
        "_index": index,
        "cross_llm_summary_md": summary_md,
        "executive_brief_md": exec_brief_md,
        "reusable_patterns": learning_list_to_output(reusable),
        "prompt_wins": prompt_wins_to_output(prompt_wins),
        "failure_patterns": failures_to_output(failures),
        "contradictions": contradictions_to_output(contradictions),
        "tasks_created": tasks_to_output(tasks),
        "decisions_detected": decisions_to_output(decisions),
        "memory_candidates": memory_to_output(memory_candidates),
        "next_best_actions": nba,
        "provider_deltas": provider_deltas,
    }


def build_next_best_actions(learnings, n):
    blockers = [l for l in learnings if l.get("learning_type") == "blocker"]
    opps = [l for l in learnings if l.get("learning_type") == "opportunity"]
    tasks = [l for l in learnings if l.get("feed_forward", {}).get("should_create_task")]

    actions = []
    for i, b in enumerate(blockers[:3]):
        actions.append({"rank": i+1, "action": f"Resolve: {b.get('title','?')}", "rationale": b.get("summary",""),
                        "urgency": "high", "assignee": "troy", "business_key": b.get("business_key"), "due_hint": None})
    for i, o in enumerate(opps[:3]):
        actions.append({"rank": len(actions)+1, "action": f"Pursue: {o.get('title','?')}", "rationale": o.get("summary",""),
                        "urgency": "medium", "assignee": "either", "business_key": o.get("business_key"), "due_hint": None})
    for i, t in enumerate(tasks[:4]):
        actions.append({"rank": len(actions)+1, "action": t.get("title","?"), "rationale": t.get("summary",""),
                        "urgency": "medium", "assignee": "agent", "business_key": t.get("business_key"), "due_hint": None})
    return actions[:n]


def build_cross_llm_summary(learnings, date_str, providers, contradictions, nba):
    top_types = {}
    for l in learnings:
        t = l.get("learning_type", "other")
        top_types[t] = top_types.get(t, 0) + 1
    type_summary = ", ".join(f"{v} {k}" for k, v in sorted(top_types.items(), key=lambda x: -x[1])[:5])
    top_action = nba[0]["action"] if nba else "none"
    return f"""# Cross-LLM Summary — {date_str}

## Summary
Processed {len(learnings)} learnings across {len(providers)} providers ({', '.join(providers)}).
Learning breakdown: {type_summary}.
{len(contradictions)} contradictions detected requiring review.

## What changed today
See provider_deltas/ for per-provider breakdown.

## Key learnings (top 5)
{"".join(f"- **{l.get('title','?')}** ({l.get('source_provider','?')}, confidence={l.get('confidence',0):.2f})\\n" for l in sorted(learnings, key=lambda x: -x.get('confidence',0))[:5])}

## Contradictions detected
{len(contradictions)} contradictions. See contradictions.json.

## Recommended next action
{top_action}

---
*Generated by llm-json-build-daily-feed at {utcnow()}*
"""


def build_executive_brief(decisions, tasks, learnings, date_str):
    blockers = [l for l in learnings if l.get("learning_type") == "blocker"]
    return f"""# Executive Brief — {date_str}

## Decisions made
{"".join(f"- {d.get('title','?')}\\n" for d in decisions) or "None detected."}

## Commitments outstanding
See decisions_detected.json for full list.

## Blockers unresolved
{"".join(f"- {b.get('title','?')}\\n" for b in blockers) or "None."}

## Immediate next actions
{"".join(f"{i+1}. {t.get('title','?')}\\n" for i, t in enumerate(tasks[:5])) or "None."}

---
*Generated at {utcnow()}*
"""


def build_provider_delta(provider, learnings, date_str):
    strengths = [l["title"] for l in learnings if l.get("learning_type") == "model_strength"]
    weaknesses = [l["title"] for l in learnings if l.get("learning_type") == "model_weakness"]
    prompt_wins = [l for l in learnings if l.get("learning_type") == "prompt_win"]
    return {
        "provider": provider, "date": date_str,
        "conversation_count": len({l.get("conversation_id") for l in learnings}),
        "learning_count": len(learnings),
        "strengths_today": strengths[:5],
        "weaknesses_today": weaknesses[:3],
        "best_prompt_type": prompt_wins[0]["title"] if prompt_wins else "unknown",
        "recommended_use": "general",
        "contradiction_rate": 0.0,
        "notes": f"{len(prompt_wins)} prompt wins today"
    }


def learning_list_to_output(learnings):
    return [{"learning_id": l.get("learning_id"), "title": l.get("title"), "summary": l.get("summary"),
             "source_provider": l.get("source_provider"), "confidence": l.get("confidence"), "tags": l.get("tags", [])}
            for l in learnings]


def prompt_wins_to_output(learnings):
    return [{"id": l.get("learning_id"), "provider": l.get("source_provider"), "prompt_summary": l.get("title"),
             "outcome_summary": l.get("summary"), "confidence": l.get("confidence", 0),
             "verbatim_prompt_excerpt": l.get("summary", "")[:400],
             "tags": l.get("tags", []), "should_add_to_prompt_pack": l.get("feed_forward", {}).get("should_update_prompt_pack", False)}
            for l in learnings]


def failures_to_output(learnings):
    return [{"id": l.get("learning_id"), "provider": l.get("source_provider"),
             "failure_type": l.get("learning_type", "prompt_fail"),
             "description": l.get("summary", ""), "root_cause": "", "avoid_pattern": l.get("title", ""),
             "mitigation": ""}
            for l in learnings]


def contradictions_to_output(learnings):
    return [{"id": l.get("learning_id"), "topic": l.get("title", ""),
             "providers_compared": [l.get("source_provider", "")],
             "position_a": l.get("summary", ""), "position_b": "",
             "agreement": "contradict", "recommended_stance": "", "needs_human_review": True}
            for l in learnings]


def tasks_to_output(learnings):
    return [{"id": l.get("learning_id"), "title": l.get("title"), "description": l.get("summary", ""),
             "assignee": "troy", "due_date_hint": None, "source_provider": l.get("source_provider"),
             "source_conversation_id": l.get("conversation_id"), "confidence": l.get("confidence", 0),
             "business_key": l.get("business_key")}
            for l in learnings]


def decisions_to_output(learnings):
    return [{"id": l.get("learning_id"), "decision": l.get("title"), "made_by": "troy",
             "date": l.get("date"), "source_provider": l.get("source_provider"),
             "confidence": l.get("confidence", 0), "reversible": True, "business_key": l.get("business_key")}
            for l in learnings]


def memory_to_output(learnings):
    return [{"id": l.get("learning_id"), "statement": l.get("title"), "confidence": l.get("confidence", 0),
             "category": "fact", "replaces_existing": None, "needs_human_review": True}
            for l in learnings]


# ─── GITHUB WRITES ────────────────────────────────────────────────────────────

def write_to_github(daily, date_str, run_id):
    base = f"daily/{date_str}"
    files = {
        f"{base}/_index.json": json.dumps(daily["_index"], indent=2),
        f"{base}/cross_llm_summary.md": daily["cross_llm_summary_md"],
        f"{base}/executive_brief.md": daily["executive_brief_md"],
        f"{base}/reusable_patterns.json": json.dumps(daily["reusable_patterns"], indent=2),
        f"{base}/prompt_wins.json": json.dumps(daily["prompt_wins"], indent=2),
        f"{base}/failure_patterns.json": json.dumps(daily["failure_patterns"], indent=2),
        f"{base}/contradictions.json": json.dumps(daily["contradictions"], indent=2),
        f"{base}/tasks_created.json": json.dumps(daily["tasks_created"], indent=2),
        f"{base}/decisions_detected.json": json.dumps(daily["decisions_detected"], indent=2),
        f"{base}/memory_candidates.json": json.dumps(daily["memory_candidates"], indent=2),
        f"{base}/next_best_actions.json": json.dumps(daily["next_best_actions"], indent=2),
    }
    for provider, delta in daily["provider_deltas"].items():
        files[f"{base}/provider_deltas/{provider}.json"] = json.dumps(delta, indent=2)

    for path, content in files.items():
        github_put(path, content, f"daily: {date_str} [{run_id}]")


def write_latest_to_github(daily):
    files = {
        "daily/latest/cross_llm_summary.md": daily["cross_llm_summary_md"],
        "daily/latest/reusable_patterns.json": json.dumps(daily["reusable_patterns"], indent=2),
        "daily/latest/prompt_wins.json": json.dumps(daily["prompt_wins"], indent=2),
        "daily/latest/next_best_actions.json": json.dumps(daily["next_best_actions"], indent=2),
    }
    for path, content in files.items():
        github_put(path, content, "chore: update daily/latest")


def update_index(date_str, run_id, learning_count):
    index = {
        "latest_date": date_str,
        "latest_run_id": run_id,
        "learning_count": learning_count,
        "updated_at": utcnow(),
        "paths": {
            "daily_latest": "daily/latest/",
            "corpus_registry": "manifests/corpus_registry.json",
            "provider_index": "manifests/provider_index.json"
        }
    }
    github_put("indexes/latest.json", json.dumps(index, indent=2), f"chore: update index for {date_str}")


def github_put(path, content, message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    # Get existing SHA
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
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"
        }
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        logger.warning(f"GitHub PUT failed {path}: {e.code} {e.read()[:200]}")


# ─── SUPABASE ─────────────────────────────────────────────────────────────────

def supabase_get(url, schema="llm"):
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Accept-Profile": schema
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.warning(f"Supabase GET failed: {e}")
        return []


def upsert_daily_digest(daily, date_str, run_id):
    providers = daily["_index"]["providers_included"]
    for provider in providers + ["cross-llm"]:
        record = {
            "date": date_str,
            "provider": provider,
            "run_id": run_id,
            "learning_count": daily["_index"]["total_learnings"],
            "prompt_wins": len(daily["prompt_wins"]),
            "contradictions": len(daily["contradictions"]),
            "decisions_detected": len(daily["decisions_detected"]),
            "tasks_created": len(daily["tasks_created"]),
            "memory_candidates": len(daily["memory_candidates"]),
            "generated_at": utcnow()
        }
        supabase_upsert_raw("llm", "daily_digest", record)


def supabase_upsert_raw(schema, table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="POST",
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
        logger.warning(f"Supabase upsert {table} failed: {e.code}")


# ─── NOTIFY ───────────────────────────────────────────────────────────────────

def notify(date_str, learning_count, contradiction_count, top_action):
    msg = (f"📊 LLM-JSON daily feed: {date_str}\n"
           f"Learnings: {learning_count} | Contradictions: {contradiction_count}\n"
           f"Top action: {top_action}")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode(),
                                  headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


# ─── UTILS ────────────────────────────────────────────────────────────────────

def utcnow():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def utcnow_compact():
    return datetime.datetime.utcnow().strftime("%H%M%S")
