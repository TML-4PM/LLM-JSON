# Contradiction Detection Prompt — Cross-Provider

## Prompt template

```
Compare these responses from different LLM providers on the same topic.
Determine if they agree, partially agree, or contradict.

Topic: {topic}

Provider A — {provider_a}:
{response_a}

Provider B — {provider_b}:
{response_b}

Return JSON:
{
  "agreement": "agree|partial|contradict",
  "delta_summary": "<core difference in plain language, max 100 chars>",
  "recommended_stance": "<which position to trust and why, 1-2 sentences>",
  "needs_human_review": <true if consequential contradiction>,
  "confidence": <0.0-1.0 confidence in your assessment>
}

Return ONLY the JSON object. No markdown. No preamble.
```

## Usage notes
- Run when same topic appears across 2+ providers in a single day
- `needs_human_review=true` → triggers Telegram notification to Troy
- agreement="contradict" + confidence>0.8 → inserted into `llm.contradictions`
- Topic grouping uses tag overlap: same 2+ tags across different provider learnings
