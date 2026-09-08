# Review And Optimization

Use this phase when the user wants to understand performance and decide next actions.

## Review Inputs

Prefer:

- platform
- account or advertiser ID
- campaign scope
- date range
- success metric

If success metric is absent, evaluate delivery using spend, impressions, clicks, CTR, CPC, conversions, CPA, ROAS, or platform-appropriate equivalents based on available data.

## Review Flow

1. Pull the requested reporting slice through `motata ad ops`
2. Identify strongest and weakest entities
3. Explain likely drivers
4. Recommend next actions

## Diagnostic Categories

Use these buckets:

1. creative or message mismatch
2. audience or targeting mismatch
3. budget or bid structure issue
4. promoted object or conversion setup issue
5. asset availability or identity setup issue
6. platform routing issue

## Required Outputs

Return a `review_report` that includes:

- scope reviewed
- key findings
- winning entities
- weak entities
- likely causes
- recommended next actions

If the user asked for a full workflow, connect the review back to:

- strategy adjustments
- delivery changes
- asset requirements for the next round
