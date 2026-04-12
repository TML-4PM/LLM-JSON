# Summarisation Prompt — Cross-LLM Daily Summary

## Prompt template

```
You are summarising learnings extracted from LLM conversations across multiple providers.

Providers included: {providers}
Date: {date}
Total learnings: {count}

Learning type breakdown:
{type_breakdown}

Top 5 learnings by confidence:
{top_learnings_json}

Contradictions detected: {contradiction_count}

Write a daily cross-LLM summary in markdown covering:

## Summary
2-3 sentences. What mattered most today.

## What changed today
Bullet list of notable shifts vs typical.

## Key learnings (top 5)
Brief bullets from top_learnings above.

## Contradictions detected
How many and the most significant topic if any.

## Recommended next action
Single most important thing to act on.

Rules:
- Concise. No padding.
- Lead with the most important thing.
- Skip sections that have nothing to report.
```

## Usage notes
- Called once per daily run by build_daily_feed
- Input: aggregated learnings JSON for the day
- Output written to: `daily/YYYY-MM-DD/cross_llm_summary.md`
