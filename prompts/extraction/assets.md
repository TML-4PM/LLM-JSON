# Extraction Prompt — Assets & URLs

Extract all asset references from the conversation.

## Prompt template

```
Extract all asset references from this conversation including:
- URLs (https://)
- S3 URIs (s3://)
- GitHub references (github.com/..., TML-4PM/...)
- Supabase tables, schemas, or views
- Lambda function names or ARNs
- API endpoints or bridge routes

For each asset return a JSON array item:
{
  "asset_type": "<url|s3_uri|github_ref|supabase_table|lambda_arn|domain|api_endpoint>",
  "locator": "<the actual reference string>",
  "context": "<why it was mentioned, max 150 chars>",
  "is_canonical": <true if described as canonical, primary, or production>
}

Return ONLY a JSON array. No markdown. No preamble.

Conversation:
{conversation_text}
```

## Usage notes
- is_canonical=true only when explicitly described as such
- Include partial references if clearly identifiable (e.g. "the S1 database")
- Normalise: strip trailing slashes from URLs
