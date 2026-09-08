# Auth And Token

`motata` supports direct tokens and skill-assisted retrieval.
The published CLI also ships the bundled `motata-token` helper, so token retrieval stays self-contained after installation.
For first-run onboarding, prefer `motata init` because it verifies token source, confirms account scope, runs a user-type classification, and suggests the next daily report command.

## Preferred Order

0. `motata init`
1. Direct `--access-token`
2. `META_ACCESS_TOKEN`, `MOTATA_META_ACCESS_TOKEN`, `TIKTOK_ACCESS_TOKEN`, or `MOTATA_TIKTOK_ACCESS_TOKEN`
3. `motata token`
4. bundled helper command `motata-token`
5. `motata-token --mode inventory` when you need to discover all linked ad accounts and attached tokens before choosing one

## Default Policy

Use Auth Center retrieval when the user has an API key but has not supplied a platform access token.

## Direct Token Account Binding

- Meta: if the user directly provides a Meta access token, you may use it immediately. When the target ad account is not specified, discover accessible accounts from the token with the Meta accounts/adaccounts command before choosing scope.
- TikTok: if the user directly provides a TikTok access token, the token alone is not enough for account-scoped operations. The user must also specify the operable advertiser/ad account ID, or you must discover it through an account inventory path before running account-scoped commands. A direct-token discovery path is: call `open_api/v1.3/bc/get/` to list accessible Business Centers, then call `open_api/v1.3/bc/asset/get/?asset_type=ADVERTISER` for each BC and use returned `asset_id` values as operable advertiser IDs.
- Auth Center: tokens fetched through Auth Center do not have this ambiguity because the account-token relationship can be queried from Auth Center inventory or account-scoped retrieval APIs.

Recommended retrieval modes:

- Meta account token: account mode by ad account ID
- TikTok account token: account mode by advertiser ID if Auth Center maps the account
- channel token: only when account-scoped retrieval is not required

## Examples

Account-scoped retrieval:

```bash
motata-token \
  --mode account \
  --account-id 1015303836971442 \
  --api-key "$AUTH_CENTER_API_KEY"
```

Legacy alias still works:

```bash
motata-auth-center-token ...
```

Then run `motata` with the returned access token:

```bash
motata meta campaigns list \
  --account 1015303836971442 \
  --access-token "$META_ACCESS_TOKEN"
```

TikTok:

```bash
motata tiktok campaigns list \
  --advertiser-id 7444033053753835536 \
  --access-token "$TIKTOK_ACCESS_TOKEN"
```

Inventory without an account ID:

```bash
motata-token \
  --mode inventory \
  --channel tiktok \
  --api-key "$AUTH_CENTER_API_KEY"
```

Use inventory mode when you do not yet know which ad account to target and want the full account-plus-token list first.

## Important Notes

- `motata` no longer depends on old XMP token fetch flows.
- The repository already contains a dedicated `motata token` skill. Reuse it when the user needs token lookup, account discovery from Auth Center, or troubleshooting around API-key retrieval.
