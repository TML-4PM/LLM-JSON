# Summarisation Prompt — Single Conversation

## Prompt template

```
Summarise this LLM conversation in 2-3 sentences.

Focus on:
- The main problem or goal discussed
- What was decided or resolved
- Any key output or artefact produced

Rules:
- Plain text only, no markdown
- No preamble ("In this conversation..." etc.)
- Max 150 words
- If nothing was resolved, say so plainly

Conversation:
{conversation_text}
```

## Usage notes
- Used during chunking to populate `llm.conversations.summary`
- Target: 50-150 words
- Called via Bedrock Claude Haiku (cost-optimised)
- Input: first 10 messages of conversation (truncated to 2000 chars)
