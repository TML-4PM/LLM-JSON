# Reuse Pattern Identification Prompt

## Prompt template

```
Identify reusable patterns in these learnings.

A reusable pattern is ONE of:
- A prompt structure that produced consistently excellent results
- A workflow sequence worth standardising and repeating
- A problem-solving approach applicable to similar future problems
- A tool or API usage pattern worth codifying

NOT a reusable pattern:
- A one-off decision
- A task or action item
- A fact specific to a single business or date

For each pattern found, return a JSON array item:
{
  "learning_type": "reusable_pattern",
  "title": "<pattern name, verb-noun format, max 60 chars>",
  "summary": "<what the pattern is and when to use it, 2-3 sentences>",
  "confidence": <0.0-1.0>,
  "reusability": "high",
  "tags": ["pattern", "<primary domain>", "<secondary domain if applicable>"],
  "feed_forward": {
    "should_update_prompt_pack": true,
    "should_update_memory": false,
    "should_create_task": false
  }
}

Return ONLY a JSON array. No markdown. No preamble.
Return empty array [] if no high-confidence reusable patterns found.

Learnings to analyse:
{learnings_json}
```

## Usage notes
- Filter threshold: confidence >= 0.75, reusability = "high" only
- Tag primary domain for routing to correct `prompts/` subfolder
- Output feeds `publish_learnings` → `prompts/{domain}/{slug}.md`
- Run on aggregated daily learnings, not per-conversation
