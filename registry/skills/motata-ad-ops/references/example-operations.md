# Example Operations

Use these examples to keep `motata ad ops` outputs concrete.

## Example 1: List TikTok Account State

Incoming request:

```json
{
  "intent": "discover",
  "platform": "tiktok",
  "account_context": {
    "advertiser_id": "7444033053753835536",
    "token_source": "auth_center"
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
  }
}
```

Expected output:

```json
{
  "asset_inventory": {
    "platform": "tiktok",
    "account_scope": "7444033053753835536",
    "usable_assets": ["campaigns", "identities", "discoverable assets"],
    "missing_prerequisites": []
  },
  "command_results": [
    {
      "step": "accounts info",
      "command_group": "tiktok accounts",
      "status": "ok",
      "summary": "advertiser reachable"
    }
  ],
  "next_safe_action": "run validation before any create flow"
}
```

## Example 2: Validate Meta Launch Readiness

Incoming request:

```json
{
  "intent": "validate",
  "platform": "meta",
  "account_context": {
    "account_id": "1015303836971442",
    "token_source": "auth_center"
  },
  "read_steps": [
    "assets discover",
    "campaigns list"
  ],
  "validation_steps": [
    "validate promoted-object",
    "validate ad-link"
  ],
  "write_steps": [],
  "constraints": {
    "allow_writes": false,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  }
}
```

Expected output:

- usable pages and pixels
- validation pass or fail summary
- missing prerequisites
- recommended next step

## Example 3: Safe Status Write

Incoming request:

```json
{
  "intent": "update",
  "platform": "tiktok",
  "account_context": {
    "advertiser_id": "7444033053753835536",
    "token_source": "direct_token"
  },
  "read_steps": [
    "campaigns get"
  ],
  "validation_steps": [],
  "write_steps": [
    "campaigns status same-value check"
  ],
  "constraints": {
    "allow_writes": true,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  }
}
```

Expected behavior:

1. confirm target object exists
2. apply only the scoped status check
3. report if the object is invalid rather than pretending the command surface is broken

## Example 4: Review Request

Incoming request:

```json
{
  "intent": "review",
  "platform": "meta",
  "account_context": {
    "account_id": "766290062019765",
    "token_source": "auth_center"
  },
  "read_steps": [
    "campaigns list",
    "insights get"
  ],
  "validation_steps": [],
  "write_steps": [],
  "constraints": {
    "allow_writes": false,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  }
}
```

Expected output:

- campaign snapshot
- performance slice summary
- strongest and weakest entities
- next safe action for optimization
