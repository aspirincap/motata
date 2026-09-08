# Strategy To Execution Handoff

Use this file when strategy research needs to become a concrete `motata ad ops` request.

## Handoff Rule

Do not send raw research notes into execution.

Always convert strategy output into these three layers:

1. `product_intake` when a URL exists
2. `campaign_brief`
3. `delivery_request`
4. `execution_request`

`product_intake` is the normalized landing or store input.
`campaign_brief` is for business intent.
`delivery_request` is for launch design.
`execution_request` is for operational execution.

## Standard `product_intake`

Use this compact shape when the work starts from a URL:

```json
{
  "offer_name": "product or app name",
  "destination_type": "website | app",
  "price_anchor": 119.99,
  "headline_candidates": [
    "top landing-page or store-page messages"
  ],
  "value_prop_candidates": [
    "compressed benefit statements"
  ],
  "cta_candidates": [
    "likely usable CTA directions"
  ]
}
```

## Standard `delivery_request`

Use this shape:

```json
{
  "platform": "meta | tiktok | both",
  "objective": "what the campaign should achieve",
  "destination": "website | app | lead form | message destination",
  "target_geography": ["US"],
  "audience_hypotheses": [
    "one or more audience hypotheses"
  ],
  "message_hypotheses": [
    "one or more message hypotheses"
  ],
  "budget_hypothesis": {
    "currency": "USD",
    "amount": 300,
    "cadence": "day | total"
  },
  "required_assets": [
    "page",
    "pixel",
    "identity",
    "media"
  ],
  "required_validations": [
    "promoted-object",
    "creative",
    "ad-link"
  ],
  "write_scope": "read_only | validate_only | create_paused | update_existing"
}
```

## Standard `execution_request`

This is the object handed to `motata ad ops`.

```json
{
  "intent": "discover | validate | create | update | review",
  "platform": "meta | tiktok",
  "account_context": {
    "account_id": "for Meta when known",
    "advertiser_id": "for TikTok when known",
    "token_source": "direct_token | env | auth_center"
  },
  "read_steps": [
    "ordered read-first tasks"
  ],
  "validation_steps": [
    "ordered validations"
  ],
  "write_steps": [
    "ordered writes or empty if not allowed"
  ],
  "constraints": {
    "allow_writes": true,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  },
  "expected_outputs": [
    "asset_inventory",
    "command_results"
  ]
}
```

## Prompt Template

Use this prompt template when handing work to `motata ad ops`:

```text
Use `motata ad ops` to execute the following plan.

Product intake:
{{product_intake}}

Platform: {{platform}}
Intent: {{intent}}
Account context: {{account_context}}
Token source: {{token_source}}

Read steps:
{{read_steps}}

Validation steps:
{{validation_steps}}

Write steps:
{{write_steps}}

Constraints:
- allow_writes: {{allow_writes}}
- default_status: {{default_status}}
- exclude_copy: {{exclude_copy}}
- exclude_aigc: {{exclude_aigc}}

Return:
1. asset_inventory
2. command_results
3. missing prerequisites
4. next safe action
```

## Translation Rules

Translate strategic statements into operational requests like this:

- "this product page looks premium but high-ticket" -> keep `price_anchor` and use it to frame budget and audience caution
- "test US first" -> discovery or validation should prioritize US-ready assets and routing
- "lead with TikTok" -> `platform=tiktok`
- "do not launch live yet" -> `write_scope=create_paused`
- "need to know if this can run" -> `intent=validate`
- "start from the accounts I already have" -> first read step is account and asset discovery

## Non-Goals

Do not turn a strategic plan into fabricated object IDs, fabricated budgets at sub-entity level, or unverified asset references. Execution must still discover and validate real account state.
