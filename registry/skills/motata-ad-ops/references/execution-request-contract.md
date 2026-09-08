# Execution Request Contract

This file defines what `motata ad ops` expects from a top-level orchestrator.

## Accepted Input Shape

Prefer an `execution_request` object with:

```json
{
  "intent": "discover | validate | create | update | review",
  "platform": "meta | tiktok",
  "account_context": {
    "account_id": "Meta account ID when relevant",
    "advertiser_id": "TikTok advertiser ID when relevant",
    "token_source": "direct_token | env | auth_center",
    "api_key_available": true
  },
  "read_steps": [],
  "validation_steps": [],
  "write_steps": [],
  "constraints": {
    "allow_writes": false,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  },
  "expected_outputs": []
}
```

## Resolution Rules

Resolve the request in this order:

1. determine platform
2. determine token path
3. determine account scope
4. run read steps
5. run validation steps
6. run writes only if allowed
7. summarize outputs and missing prerequisites

## Output Shape

Return:

```json
{
  "asset_inventory": {
    "platform": "meta | tiktok",
    "account_scope": "resolved account or advertiser",
    "usable_assets": [],
    "missing_prerequisites": []
  },
  "command_results": [
    {
      "step": "human-readable step name",
      "command_group": "motata namespace",
      "status": "ok | failed | skipped",
      "summary": "short result"
    }
  ],
  "next_safe_action": "recommended next action"
}
```

## Prompt Template

Use this prompt for direct execution requests:

```text
Use `motata ad ops` to fulfill this execution request.

Execution request:
{{execution_request}}

Rules:
- prefer read-first
- validate before create when possible
- use Auth Center token retrieval if direct access token is absent
- do not use copy flows
- do not use AIGC unless explicitly requested

Return:
- asset_inventory
- command_results
- next_safe_action
```

## Minimal Example

```json
{
  "intent": "discover",
  "platform": "tiktok",
  "account_context": {
    "advertiser_id": "7444033053753835536",
    "token_source": "auth_center",
    "api_key_available": true
  },
  "read_steps": [
    "accounts info",
    "assets discover",
    "identities list",
    "campaigns list"
  ],
  "validation_steps": [],
  "write_steps": [],
  "constraints": {
    "allow_writes": false,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  },
  "expected_outputs": [
    "asset_inventory",
    "campaign_snapshot"
  ]
}
```
