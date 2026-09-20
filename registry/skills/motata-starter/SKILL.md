---
name: motata-starter
description: |
  End-to-end Meta and TikTok advertising orchestrator built on motata. Use when the user wants a full ads workflow that combines strategy research, delivery execution, and post-campaign review, or when they ask for "广告全流程", "广告策略与交付总编排", "launch campaign from scratch", "plan then execute ads", "review and optimize campaigns", or "run Meta/TikTok ads end to end". Also use when the user asks to turn a product URL or product page into a cross-channel launch plan, such as "根据这个产品设计一个跨渠道投放方案，日预算1000刀", "给我一个产品页的投放策略", or similar budgeted channel-planning requests. Prefer the first-class `motata product intake` path whenever a URL exists. This skill routes work across three phases: strategy research, delivery execution, and review. It uses `motata ad ops` for operational execution and reuses `motata token` for token retrieval. It does not do creative production.
---

## Gateway mode takes precedence

When `MOTATA_AUTH_MODE=gateway`, do not run `motata-token`, fetch platform tokens
with curl, paste secrets, use `--access-token`, or read Auth Center API keys. All
token-fetch examples below and in referenced files apply only to explicitly
trusted human/admin **direct mode**, never to a Gateway agent. The Gateway holds
Meta/TikTok credentials; the CLI uses only its own limited Gateway JWT/session.

For `AUTH_REQUIRED`, use a preconfigured external issuer login (`motata gateway
--config <private-config> --session <private-session> login`) or ask the trusted
administrator to restore access. Never downgrade to direct mode. Gateway `init`
is not yet implemented; do not route Gateway onboarding through the legacy
interactive token flow. Unsupported endpoints fail closed and remain migration
work, not permission to fetch credentials. Keep write approvals and existing
migration `needs_review` rules; read partial report manifests before continuing.


# Motata Starter

Use this skill as the top-level controller for ads work. It decides which phase the task belongs to, gathers the minimum required inputs, and routes operational work to `motata ad ops`.

If the task is broad, end-to-end, or the user appears new to the stack, read [onboarding.md](references/onboarding.md) first.

## What This Skill Owns

This skill covers three phases:

0. Product intake when a URL exists
1. Strategy research
2. Delivery execution
3. Post-campaign review

This skill does not generate image or video creatives. It may define a creative plan, asset requirements, or testing matrix, but it stops before creative production.

## Product and Execution Boundaries

The product intake CLI returns extracted facts and strategy hints, not a complete budget allocation or validated experiment matrix. Build that plan explicitly from user-confirmed goals and budget; distinguish evidence from recommendations.

Reports require separate collection, diagnosis and HTML rendering steps. Check completeness and currency before making budget recommendations. Execution requires explicit approval for writes, including PAUSED creation and live validation probes. Migration recovery uses the saved job ledger; never turn a partial failure into a fresh blind retry.

## Routing Rules

Read [workflow-map.md](references/workflow-map.md) first for any non-trivial task.
If the task maps to a recognizable business use case, read [agent-routing-table.md](references/agent-routing-table.md) before choosing the module path.

Then route by intent:

1. User wants market, channel, audience, budget, or testing advice
   Use [strategy-research.md](references/strategy-research.md).
2. User wants to list accounts, inspect assets, validate payloads, create or update delivery objects, or fetch performance data
   Use `motata ad ops`, then read [delivery-routing.md](references/delivery-routing.md).
3. User wants diagnosis, optimization, or a next-step plan for running campaigns
   Use [review-and-optimization.md](references/review-and-optimization.md).

## Required Intake

Before doing substantial work, load [intake-contract.md](references/intake-contract.md) and collect the smallest viable set of inputs for the active phase.

Do not ask for every possible field up front. Only require what is necessary for the current phase.

When the user is new to the stack or still has not settled token source, account scope, or baseline metrics, start with `motata init` before routing deeper execution.

## Execution Policy

When a task enters delivery execution:

1. Reuse `motata token` to obtain account-scoped or channel-scoped tokens if the user did not already provide a direct access token.
2. Hand off command selection and guardrails to `motata ad ops`.
3. Prefer read-first discovery, then validation, then writes.
4. Default to paused or non-destructive flows unless the user explicitly asks to activate or mutate live delivery.

Read [delivery-routing.md](references/delivery-routing.md) before execution and [output-contracts.md](references/output-contracts.md) before returning structured results.

## Review Loop

A complete lifecycle should usually look like this:

1. Produce a `campaign_brief`
2. Produce an `execution_plan`
3. Execute discovery, validation, and selected delivery operations
4. Produce a `review_report`
5. Produce `next_actions`

Use [output-contracts.md](references/output-contracts.md) for those artifacts.

## Platform Selection

Choose the platform this way:

1. Use Meta when the user is working on Facebook or Instagram delivery, Pages, pixels, or Meta migration/debug tasks.
2. Use TikTok when the user is working on advertiser accounts, identities, SmartPlus flows, TikTok targeting, or TikTok reporting.
3. Use both when the user asks for a cross-platform launch plan, account comparison, or post-campaign review across channels.

If the user has not specified a platform, infer it from the assets, account IDs, or prior context. If it is still ambiguous and the choice matters, ask one concise question.

## References

- [onboarding.md](references/onboarding.md): bilingual onboarding for this skill stack
- [agent-routing-table.md](references/agent-routing-table.md): scenario-to-module routing table for agents
- [product-intake.md](references/product-intake.md): strategy-layer product intake workflow and CLI entry
- [workflow-map.md](references/workflow-map.md): phase map and routing
- [intake-contract.md](references/intake-contract.md): required inputs by phase
- [strategy-research.md](references/strategy-research.md): research method and outputs
- [strategy-to-execution-handoff.md](references/strategy-to-execution-handoff.md): standard handoff template into `motata ad ops`
- [delivery-routing.md](references/delivery-routing.md): how to route work into `motata ad ops`
- [review-and-optimization.md](references/review-and-optimization.md): review framework
- [output-contracts.md](references/output-contracts.md): structured artifacts
- [example-workflows.md](references/example-workflows.md): example tasks with input and output shapes
