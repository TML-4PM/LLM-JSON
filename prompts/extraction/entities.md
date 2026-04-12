# Extraction Prompt — Entities

Extract named entities from the conversation.

## Prompt template

```
Extract all named entities from this conversation.

Entity types to capture:
- person: named individuals
- system: software, tools, services, APIs, Lambda functions
- org: companies, teams, departments
- product: products, features, SKUs
- domain: URLs, domains, hostnames
- business_key: T4H canonical business keys (e.g. CORE_ATLAS, SYNAL, TRADIE)

For each entity return a JSON array item:
{
  "entity": "<canonical name>",
  "entity_type": "<type from list above>",
  "mention_count": <integer>,
  "notable": <true if mentioned 3+ times or central to discussion>
}

Return ONLY a JSON array. No markdown. No preamble.

Conversation:
{conversation_text}
```

## Usage notes
- Deduplicate variants: "Lambda" and "AWS Lambda" = same entity
- business_key type: only use for T4H canonical keys, not generic business names
- notable=true triggers entity registration in llm.entities
