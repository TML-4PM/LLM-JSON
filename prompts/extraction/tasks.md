# Extraction Prompt — Tasks

Extract action items and tasks from the conversation.

## Prompt template

```
Extract all tasks, action items, and next steps from this conversation.

Include:
- Explicit "I will..." or "We need to..." statements
- Implied action items from problem discussions
- Follow-up items referenced

For each task return a JSON array item:
{
  "learning_type": "commitment",
  "title": "<action item, verb-first, max 80 chars>",
  "summary": "<context and why this needs to happen, 1-2 sentences>",
  "confidence": <0.0-1.0>,
  "reusability": "none",
  "tags": ["task", "<domain>"],
  "feed_forward": {
    "should_update_prompt_pack": false,
    "should_update_memory": false,
    "should_create_task": true
  }
}

Return ONLY a JSON array. No markdown. No preamble.

Conversation:
{conversation_text}
```

## Usage notes
- confidence >= 0.85 triggers automatic task creation downstream
- Verb-first titles: "Deploy X", "Fix Y", "Review Z", "Lodge Z"
- Include assignee hint in tags: "troy", "agent", "unknown"
