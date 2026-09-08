# Motata Token

This reference mirrors the current external API surface described in `OPENAPI.md`.

## Base URL

`http://localhost:8000`

## Auth Headers

The backend accepts either of these header styles:

```bash
-H "X-API-Key: ak_xxx"
```

```bash
-H "Authorization: Bearer ak_xxx"
```

## Scope Map

| Scope | Use |
| --- | --- |
| `token:read` | Read token payloads and access tokens |
| `account:read` | Read ad account data |
| `account:sync` | Trigger ad account sync |
| `agent:read` | Read authorization agents |
| `agent:revoke` | Revoke authorizations |

## Endpoint Matrix

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/openapi/tokens` | List token records |
| `GET` | `/api/v1/openapi/tokens/{channel}:{agent_id}` | Get one token record |
| `GET` | `/api/v1/openapi/tokens/{channel}:{agent_id}/access-token` | Get raw access token |
| `POST` | `/api/v1/openapi/billing/token-usage` | Report downstream token usage |
| `GET` | `/api/v1/openapi/ad-accounts` | List ad accounts |
| `GET` | `/api/v1/openapi/ad-accounts/access-tokens` | List ad accounts with attached tokens |
| `GET` | `/api/v1/openapi/ad-accounts/{account_id}` | Get one ad account |
| `GET` | `/api/v1/openapi/ad-accounts/{account_id}/token` | Get token for one ad account |
| `POST` | `/api/v1/openapi/ad-accounts/sync` | Trigger ad-account sync |
| `GET` | `/api/v1/openapi/agents/channel/{channel}/valid-token` | Get freshest valid token for a channel |
| `GET` | `/api/v1/openapi/agents` | List agents |
| `DELETE` | `/api/v1/openapi/agents/{channel}/{agent_id}` | Revoke authorization |

## Common Retrieval Patterns

### Agent token

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/tokens/google:107299195285503777055/access-token"
```

### Ad account token

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/ad-accounts/7444033053753835536/token"
```

### Full ad-account inventory with tokens

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/ad-accounts/access-tokens"
```

This endpoint does not require an account id. Use it when you need to discover the tenant's full ad-account inventory and attached tokens first.

Filter inventory by channel:

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/ad-accounts/access-tokens?channel=tiktok"
```

Extract account IDs and tokens:

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/ad-accounts/access-tokens" \
  | jq '.data.accounts[] | {account_id, channel, token: .token.access_token}'
```

Compact helper output from the installed CLI (reads `AUTH_CENTER_API_KEY` from the environment):

```bash
motata-token \
  --mode inventory \
  --channel tiktok \
  --compact
```

### Ad-account list

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/ad-accounts?channel=meta&page=1&page_size=20"
```

### Fresh channel token

```bash
curl -s \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/agents/channel/google/valid-token"
```

### Sync ad accounts

```bash
curl -s -X POST \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  "http://localhost:8000/api/v1/openapi/ad-accounts/sync?channel=google"
```

### Report token usage

```bash
curl -s -X POST \
  -H "X-API-Key: ${AUTH_CENTER_API_KEY}" \
  -H "Content-Type: application/json" \
  "http://localhost:8000/api/v1/openapi/billing/token-usage" \
  -d '{
    "channel": "google",
    "account_id": "1234567890",
    "units": 1,
    "endpoint": "/google-ads/customers:listAccessibleCustomers"
  }'
```

## Response Notes

- Successful responses are wrapped in `{ "success": true, "data": ..., "meta": ... }`
- `ad-accounts/access-tokens` returns `data.accounts`; some responses also include pagination or count metadata depending on the request shape
- If no valid token exists for a channel, the `valid-token` endpoint may return `token: null`

## Common Errors

| HTTP | Meaning |
| --- | --- |
| `400` | Invalid params, invalid channel, or inactive token |
| `401` | Missing, invalid, or expired API key |
| `402` | Billing quota exhausted |
| `403` | Missing required scope |
| `404` | Agent, token, or ad account not found |
