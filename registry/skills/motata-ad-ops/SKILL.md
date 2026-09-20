---
name: motata-ad-ops
description: |
  Safe operational execution layer for Meta and TikTok ads work through motata. Use when the user wants to list accounts, inspect campaigns, discover usable assets, validate promotable objects, run insights, analyze Meta/TikTok landing pages or product-level spend, infer an advertiser/user type from Meta or TikTok token spend + URL/app evidence, troubleshoot API failures, or perform carefully controlled writes with motata. Triggers include "motata", "列出广告账户", "list campaigns", "discover assets", "validate promoted object", "run TikTok insights", "Meta debug", "migrate assets", "Meta落地页", "TikTok落地页", "按产品看消耗", "landing page spend", "用户类型判定", "广告主类型", "classify user type", or "inspect ad account state". This skill reuses `motata token` and the bundled `motata-token` helper for token retrieval, and it focuses on read-first, validate-first execution.
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


# Motata Ad Ops

Use this skill for direct operational work through the `motata` CLI.

If the task is concrete but the user is new to `motata`, read [onboarding.md](references/onboarding.md) first.

## What This Skill Owns

This skill handles:

1. token retrieval path selection
2. Meta and TikTok command routing
3. discovery and validation before writes
4. safe operational execution
5. troubleshooting and migration/debug support

For exact top-level lifecycle orchestration, use `motata starter`.

## Invocation Rule

Prefer the documented command surfaces in the repository references instead of guessing.

If exact flags are unclear, run `motata <group> <action> --help` and then execute the command.

## Token Retrieval

Read [auth-and-token.md](references/auth-and-token.md) before account-scoped execution.

Prefer this order:

0. `motata init` when the user is onboarding a new machine, tenant, or account and needs one guided pass for token source, account selection, user type, and metric defaults
1. user-provided `--access-token`
2. shell environment token already available
3. `motata token` skill or bundled `motata-token`
4. `motata-token --mode inventory` when you need to discover linked accounts and attached tokens before choosing an account

Do not use old XMP-based token acquisition commands.

## Platform Routing

1. For Meta tasks, read [meta-playbooks.md](references/meta-playbooks.md).
2. For TikTok tasks, read [tiktok-playbooks.md](references/tiktok-playbooks.md).
3. For any write or risky action, read [safe-write-rules.md](references/safe-write-rules.md) first.
4. For errors or unexpected payload behavior, read [troubleshooting.md](references/troubleshooting.md).

## Execution Safety Contract

- `PAUSED` creates real platform objects; it is not dry-run. Meta `validate creative`, `validate ad-link` and `validate promoted-object` are live probes. Obtain explicit approval before adding `--allow-live-probe`; `--cleanup` is a best-effort remote deletion, not a guarantee of no residual objects.
- Read process exit codes and structured execution/completeness state. A partial result is not success; preserve created-object IDs and report limitations before continuing.
- Never automatically retry a timed-out write. Inspect the saved migration ledger and remote state. `needs_review` requires reconciliation; matching names are not evidence of idempotency.
- Do not combine spend or revenue across currencies without an explicit conversion policy. Unknown currency must remain account-isolated. Truncated pagination prevents claiming complete account coverage.
- Use migration `resume` with the original job ID, not a fresh `run`. Legacy jobs without a versioned ledger cannot safely resume.

## Core Rules

1. Prefer read-first workflows.
2. Discover assets before referencing them in write payloads.
3. Validate promotable objects, ad links, or creatives before create flows when possible.
4. Use paused or equivalent non-live status by default for creation flows unless the user explicitly requests activation.
5. For large or risky mutations, summarize the planned command before running it.

## Supported Work

Meta:

- assets, pages, pixels, and promotable page discovery
- campaigns, adsets, ads, creatives list, get, create, update
- targeting research
- insights retrieval
- landing page and product-level spend aggregation
- advertiser/user vertical classification from top account/campaign spend plus URL/app-store evidence
- creative, ad-link, and promoted-object validation
- migration planning and execution
- Graph API debugging

TikTok:

- advertiser account discovery and inspection
- campaigns, adgroups, ads, SmartPlus entities list, get, create, update, status
- identities, creative-assets, media, images, videos, assets discovery
- public post/item metadata resolution by item_id, including real handle, @username, avatar, preview image, real post URL, upload time, and optional image downloads
- targeting helpers
- insights retrieval
- landing page, app discovery, and user-type classification
- creative, ad-link, and promoted-object validation

This skill does not cover AIGC execution by default unless the user explicitly asks for it.

## References

- [onboarding.md](references/onboarding.md): bilingual operational onboarding
- [auth-and-token.md](references/auth-and-token.md)
- [execution-request-contract.md](references/execution-request-contract.md)
- [meta-playbooks.md](references/meta-playbooks.md)
- [user-type-classification.md](references/user-type-classification.md)
- [tiktok-playbooks.md](references/tiktok-playbooks.md)
- [safe-write-rules.md](references/safe-write-rules.md)
- [troubleshooting.md](references/troubleshooting.md)
- [example-operations.md](references/example-operations.md)
