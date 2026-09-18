# Auth Center credential integration (experimental increment 2)

This change connects **the Gateway server** to the existing Motata Auth Center
OpenAPI. It does not move platform secrets to the CLI, create an OAuth issuer,
complete the full CLI migration, or deploy either service.

Implementation baseline: Motata `0ddc76d23561c23c062a155bb950f284ac1cde5c`.
Reviewed Auth Center contract: `96fda9454c02cc15608d89a41333fe8e4283c5d1`.

## What works now

```text
CLI -- HTTPS + dedicated Gateway JWT --> Gateway
  Gateway JWT + local grants + strict local account binding
    -> AuthCenterProvider (server-only service API key)
    -> GET /api/v1/openapi/tokens/{channel}:{oauth_agent_id}
    -> bounded in-memory lease, injected only into Meta/TikTok request
    -> existing safe response pipeline -> CLI
```

The provider validates the returned OAuth identity, exact channel, active status,
credential format, and explicit expiration metadata. `facebook` and `meta` are
supported as explicit choices, not automatic fallback alternatives. It never
uses `/ad-accounts/{id}/token`, channel `valid-token`, or bulk token inventory.
Those legacy account-selection paths may fall back to a channel-wide token.

**Important boundary:** the current OpenAPI endpoint establishes the OAuth
identity, NOT its relationship to a requested ad account. A trusted administrator
must verify and declare that relationship. This is a strict, offline configured
binding, not automatic authoritative account discovery. It is separately checked
against the caller's local Gateway grant on every request. The Auth Center API
key is tenant-scoped; its tenant prefix is checked for configuration mistakes,
while authentication still belongs to Auth Center.

## Things this increment deliberately does not claim

- Auth Center browser login currently uses a different JWT contract. It is NOT
  accepted as a Gateway JWT merely because both use JWT. A dedicated asymmetric
  Gateway issuer (`aud=motata-gateway`, `typ=at+jwt`, required claims) is still
  required. Do not weaken `JWTVerifier` to accept the browser HS256 token.
- Users/sessions/grants have not yet been moved to the Auth Center database.
  Gateway's existing protected local grant/revocation store remains authoritative.
- No online JWKS loader, CLI login/refresh flow, or remote grant-introspection API
  was added. No Auth Center repository or deployment was modified.
- The current token response does not promise `token_version`. We preserve it
  when supplied, otherwise it is `None`; there is no invented version or claimed
  push-invalidation integration.
- Full reports, TikTok SDK writes, streaming uploads, and other unmigrated CLI
  capabilities remain blocked by the existing release gate.
- Authorization-center storage encryption and raw OAuth `auth_data` cleanup need
  their own changes. This adapter cannot make those database changes remotely.

## Install / migrate on the trusted server

Use the feature branch's gateway extra, under the protected Gateway OS identity.
Neither the platform tokens nor the Auth Center service API key may exist in the
agent environment. Provision the service key via a secret manager or trusted
hidden-input flow, NOT shell arguments, a chat, or agent-readable configuration.

Stop the Gateway before the following schema migration, and take a protected
backup of the state directory. The existing `init` command is idempotent and adds
`provider`, `binding`, and `revision` columns without deleting direct credentials,
grants, ownership or write receipts:

```bash
motata-gateway-admin --state-dir /srv/motata/private init
```

Starting new code against unmigrated state fails with a safe configuration error.
The original direct credential mode is retained, but an `auth_center` reference
can never silently resolve to an old direct credential.

## Protected provider profiles

Create `/srv/motata/config/auth-center-profiles.json` with mode `0600`, owned by the
Gateway service user. All values below are examples, not live credentials.

```json
{
  "profiles": [
    {
      "name": "company-center",
      "tenant_id": "11111111-1111-4111-8111-111111111111",
      "origin": "https://auth.example.com",
      "api_key_file": "/srv/motata/secrets/auth-center.key",
      "cache_ttl": 15,
      "timeout": 10,
      "expiry_buffer": 30
    }
  ]
}
```

The secret file must be an absolute, regular, non-symlink, private file owned by
that user. POSIX ownership/permission enforcement is implemented; Windows ACL
isolation remains a separate deployment acceptance item. The key must be a valid
Auth Center key for the configured tenant and have `token:read` AND
`token:deliver` scopes. Use a dedicated key, not a human administrator's broad key.

Only HTTPS origins on port 443 are accepted. Paths, query strings, fragments,
userinfo, arbitrary redirects, cookies and proxy environment inheritance are
not used. API keys and profile files are never accepted in client requests.
Profile changes require service restart; **API key file replacement is detected
on each resolution**, including cache hits. A missing/unsafe key file fails closed.

Add the optional field to the protected Gateway JSON config (also mode `0600`):

```json
{
  "state_dir": "/srv/motata/private",
  "issuer": "https://issuer.example.com",
  "audience": "motata-gateway",
  "jwks_path": "/srv/motata/config/gateway-public-jwks.json",
  "algorithm": "ES256",
  "meta_version": "v23.0",
  "auth_center_profiles": "/srv/motata/config/auth-center-profiles.json"
}
```

`meta_version` is an illustrative existing-test value. Select and review the
actual supported platform version before any live validation. No live platform
compatibility is inferred from the offline mocks.

## Declare explicit account / OAuth identity bindings

Verify the relationship in the trusted authorization management context FIRST.
The flag below records administrator acknowledgement; it is not an automated
proof of platform membership. Auth Center's `Agent` is a platform OAuth identity,
not the AI agent/CLI caller.

```bash
motata-gateway-admin --state-dir /srv/motata/private bind-auth-center \
  --ref meta-main \
  --profile company-center \
  --tenant 11111111-1111-4111-8111-111111111111 \
  --platform meta --channel meta --account 123 \
  --oauth-agent-id oauth-user-1 --verified-binding

motata-gateway-admin --state-dir /srv/motata/private grant \
  --subject agent-a --client motata-cli \
  --workspace 11111111-1111-4111-8111-111111111111 \
  --platform meta --account 123 --ref meta-main
```

For a legacy `facebook` authorization row, explicitly choose `--channel facebook`.
For TikTok use `--platform tiktok --channel tiktok` and its actual OAuth identity.
`workspace` must equal the binding's canonical Auth Center tenant UUID. A binding
is for one account; separate bindings may share the same underlying OAuth
identity. Never copy an AI agent ID into `--oauth-agent-id` by assumption.

Replacing a local direct reference with an Auth Center binding erases its stored
ciphertext for that reference and increments its revision. Confirm old local
grants remain appropriate. To disable access immediately at the Gateway:

```bash
motata-gateway-admin --state-dir /srv/motata/private disable-credential --ref meta-main
# Or use remove-grant / revoke for a caller or session.
```

All administration remains offline/server-side. No credential export/admin HTTP
route was added. `status` prints provider names/revisions, not service API keys,
platform secrets, or remote OAuth payloads.

## Cache, revocation and failures

- Per-OAuth-identity singleflight across concurrent account requests, with account
  authorization checked independently for every caller. Key includes profile,
  origin, tenant, platform, exact channel/identity and service-key generation.
- Maximum 256 cached entries and 16 simultaneous different-identity fetches;
  positive TTL defaults to 15 seconds (configurable 0–60), negative TTL 1 second.
- Expiration is bounded by both monotonic TTL and reported UTC expiry minus the
  safety buffer. TTL starts at request start. Unknown expiry (`null`) is allowed
  only with the same short TTL; missing/invalid timezone metadata is rejected.
- No stale-on-error fallback. 401/403, quota blocks, expiry, 429, timeouts,
  malformed/oversized responses and redirections produce safe failures. No raw
  upstream body/exception is returned. Auth Center responses are capped at 128 KiB.
- An existing valid cache entry can survive remote revocation/quota exhaustion
  until TTL; **this is NOT immediate Auth Center revocation**. `cache_ttl=0`
  disables completed-positive reuse. Shared in-flight fetches and in-flight
  platform calls still have race windows. Immediate local disable/revoke is
  checked before dispatch even with a warm credential cache.
- A local binding changed while a fetch is waiting is rejected. Session/grants,
  ownership and JWT expiry are rechecked after the await and before sending.
- HTTP 401 or reviewed Meta error 190 invalidates the relevant cached lease for
  the NEXT request; it never blindly retries the current read or write.
- Provider does not refresh platform OAuth tokens. Existing Auth Center scheduler
  remains responsible; no second competing refresh loop was introduced.
- Canceling one waiter does not cancel a shared fetch for others. Fetches have
  bounded deadlines; service close cancels outstanding fetches and drops caches.

The service key and platform lease are confined to trusted process memory. The
provider does not persist fetched platform credentials or raw OAuth payloads in
Gateway SQLite, reports, receipts or CLI output. Python memory is not claimed to
be securely zeroizable. Platform response checks include both platform token and
service key as known sensitive values.

## Writes and provider failures

Previously `pending` was saved before resolving credentials. Network-backed
resolution could therefore quarantine a write that was never sent. Now:

```text
validate JWT/grants/policy -> check saved receipt -> resolve credential
-> recheck authorization after await -> atomically begin_write -> send once
```

Credential fetch failures return `write_outcome=not_sent` and create no pending
receipt. A later retry with the same key can execute after authorization recovers.
An already-completed receipt is replayed only after current authorization checks,
without resolving another secret. Once a platform write may have been sent, the
existing pending/unknown quarantine remains in force. Cancellation is not proof
that a write failed.

## Offline validation

```bash
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_gateway.py
python scripts/check_release.py --pack-only
python scripts/check_gateway.py --release  # expected to fail: full CLI coverage outstanding
```

The new tests emulate the reviewed Auth Center endpoint and the platform using
HTTPX MockTransport/ASGITransport; real network access is prohibited by the test
runner. Tests cover provider contracts, 50-call singleflight, API-key rotation,
TTL/expiry, cancellation, binding races, response safety, zero credential requests
for invalid JWT/grants, and write receipt correctness. They are NOT live Auth
Center/Meta/TikTok validation or production security certification.

## Next integration boundary (requires Auth Center changes)

1. Add dedicated Gateway JWT issuer/CLI browser login and limited refresh.
2. Separate platform OAuth identities from CLI principals and account grants.
3. Add authenticated server-only strict account binding + lease/version contract,
   avoiding legacy channel fallback. Until then administrator bindings are required.
4. Add authoritative session/grant checks and bounded revocation propagation.
5. Add version events and quota policy that applies independently of token caching.
6. Harden Auth Center credential encryption and remove raw-token duplicates in
   `auth_data`. Only after this can it be treated as the unified hardened store.

Sources reviewed (pinned, not claims about a running deployment):
- Auth Center OpenAPI: `aspirincap/motata-auth-center` @ `96fda9454c02cc15608d89a41333fe8e4283c5d1`,
  `backend/app/api/v1/openapi.py`, `backend/app/services/token_service.py`.
- Auth Center identity mismatch: same commit, `backend/app/core/security.py`,
  `backend/app/core/config.py`, `backend/app/models/database.py`.
- HTTPX async client lifecycle/streaming: https://www.python-httpx.org/async/
- Distinct resource JWT access-token audience: https://www.rfc-editor.org/rfc/rfc9068.html
