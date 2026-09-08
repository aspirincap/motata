# Delivery Routing

This file explains how the top-level orchestrator should use `motata ad ops`.

## Default Route

For any execution task:

1. Retrieve or confirm token path
2. Discover usable account assets
3. Validate promotable objects and routing assumptions
4. Run selected delivery commands
5. Summarize results in structured output

## Token Path

Prefer this order:

1. user-provided direct access token
2. `motata token` for account or channel retrieval

Do not ask the user to paste more credentials if a valid API-key-based retrieval path already exists.

## Meta Route

Typical sequence:

1. account discovery
2. assets discovery
3. page or pixel checks if needed
4. creative, ad-link, or promoted-object validation
5. campaign, ad set, creative, ad, or migrate/debug command

## TikTok Route

Typical sequence:

1. advertiser discovery
2. assets or identities discovery
3. promoted-object or creative validation
4. campaign, adgroup, ad, SmartPlus, insights, or asset command

## Write Discipline

Before any create or update:

1. confirm the target platform and account
2. confirm the user asked for a write
3. prefer paused status or equivalent safe state
4. summarize the intended operation before execution if the change is not trivial

Use `motata ad ops` references for exact playbooks.
