# Extraction Prompt — Decisions

Extract all decisions and commitments from the conversation.

## Prompt template

```
You are extracting decisions from an LLM conversation log.

Identify every instance where:
- A choice was made between options
- An action was committed to
- A direction was set
- Something was agreed upon

For each decision, return a JSON array item:
{
  "learning_type": "decision",
  "title": "<short description of the decision, max 80 chars>",
  "summary": "<what was decided and why, 1-2 sentences>",
  "confidence": <0.0-1.0>,
  "reusability": "low",
  "tags": ["decision", "<domain>"],
  "feed_forward": {
    "should_update_prompt_pack": false,
    "should_update_memory": true,
    "should_create_task": false
  }
}

Return ONLY a JSON array. No markdown. No preamble.

Conversation:
{conversation_text}
```

## Usage notes
- confidence = 1.0 for explicit decisions, 0.6-0.8 for implied
- Tag with domain: infrastructure, financial, product, legal, technical
- Only include decisions that reached a conclusion — not discussions
