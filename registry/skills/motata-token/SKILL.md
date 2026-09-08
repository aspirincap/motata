---
name: motata-token
description: Fetch access tokens, ad-account inventories, sync status, and token-usage payloads from Auth Center OpenAPI using API keys. Use when Codex needs to retrieve a token for an agent, ad account, or channel; list ad accounts with attached tokens; trigger account sync; inspect agents; or troubleshoot 401/402/403/404 responses.
---

# Motata Token

## Overview

Use this skill for Auth Center's tenant-scoped external API under `/api/v1/openapi`.
Prefer `motata-token` for repeatable retrieval, and fall back to `curl` when you need the raw response envelope or to inspect a new endpoint.
The same helper logic is bundled into the published `motata` CLI as `motata-token`, so token retrieval stays self-contained after installation.

## Recommended Flow

0. If the user is still deciding token source, account scope, user type, or default metrics, prefer `motata init` first and then reuse this skill only for the narrower token step.

1. Confirm the service is reachable:
```bash
curl -s http://localhost:8000/health
```

2. Pick the narrowest endpoint:
- Known agent -> `GET /api/v1/openapi/tokens/{channel}:{agent_id}/access-token`
- Known ad account -> `GET /api/v1/openapi/ad-accounts/{account_id}/token`
- Need the tenant's ad-account inventory -> `GET /api/v1/openapi/ad-accounts/access-tokens`
- Need the freshest channel token -> `GET /api/v1/openapi/agents/channel/{channel}/valid-token`
- Need to refresh mappings or sync newly linked accounts -> `POST /api/v1/openapi/ad-accounts/sync`

3. Use `motata-token` for agent/account/inventory retrieval. It selects the correct runtime for both npm and Python installations.

4. Treat successful token output as secret-bearing data. Capture it privately for the authorized operation; never paste raw envelopes, compact token rows, or credentials into chat, reports, or diagnostic logs. `--json` is not a safe debug view.

## Scripted Access

`motata-token` supports:
- `--mode agent` with `--agent-id channel:agent_id`
- `--mode account` with `--account-id`
- `--mode inventory` with optional `--channel`, `--account-status`, `--page`, `--page-size`
- `--compact` in inventory mode to print TSV rows of `channel`, `account_id`, `access_token`, and `account_name`
- `--mode channel` with `--channel`
- `--header bearer` when a downstream system only accepts `Authorization: Bearer`

Inventory mode is the right choice when you want all ad accounts plus their attached tokens, including TikTok.
Use it when you do not yet have a specific account id and need discovery first.

Example:
```bash
motata-token \
  --mode inventory \
  --channel tiktok \
  --json
```

## Scopes and Behavior

- `token:read` for token payloads and access-token delivery
- `account:read` + `token:read` for account token inventory and single-account token lookup
- `agent:read` + `token:read` for channel valid-token lookup
- `account:sync` for ad-account synchronization
- `agent:revoke` for authorization revocation

Token-delivery endpoints are billing-gated. If you get `402 Payment Required`, the tenant quota is exhausted and token issuance may be blocked.

When a `valid-token` endpoint returns `token: null`, treat it as "no valid token found for that channel."

## Troubleshooting

- `401` -> API key missing, expired, or wrong header style
- `403` -> missing scope
- `404` -> agent, account, or token not found
- `402` -> billing quota exhausted

Inspect only a redacted summary of `meta`, `pagination`, account IDs and names when troubleshooting. Error responses and absent-account lookups must not dump other accounts' tokens. The helper reads `AUTH_CENTER_API_KEY` from the environment. The optional `scripts/fetch_token.py` delegates to `motata-cli` installed in that Python interpreter; npm users should use `motata-token` because its Python package lives in a private runtime.

## Reference Files

- `references/openapi-endpoints.md` has the endpoint matrix, curl examples, and response notes.
- `scripts/fetch_token.py` is the repeatable retrieval helper.
