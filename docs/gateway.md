# JWT Gateway — experimental implementation

**This branch is a working first increment, not the completed full-CLI migration and not a production-certified release.** Unsupported gateway commands fail explicitly. The original CLI remains available in direct mode; there is no automatic fallback from gateway mode to direct tokens.

## Architecture

```text
Original CLI command handlers
  -> typed GatewayAuthRef (no platform token)
  -> RemoteGatewayTransport (HTTPS + short-lived Gateway JWT)
  -> JWT signature/claims + live revocation + scope + account grant
  -> reviewed endpoint and object ownership
  -> protected encrypted credential store
  -> bounded upstream HTTP request, with server-side platform authentication
  -> response safety and opaque pagination
```

The gateway never accepts an arbitrary upstream origin, authentication header, server file path, Python code, or shell command. Gateway JWTs authenticate the CLI and are never forwarded as platform credentials. They remain bearer capabilities and are not protected against copying by the agent that legitimately possesses them.

The gateway must run on a separate server or OS identity from the agent. Encrypting a file is not isolation when the agent can read its master key, control the service, or access the host administrator/Docker socket.

## Implemented scope

End-to-end offline integration tests exercise the **existing CLI** for:

- Meta campaign list through JWT gateway;
- TikTok campaign list through JWT gateway (raw HTTP path, no SDK token runtime);
- Meta campaign creation with a caller-supplied stable idempotency key and PAUSED default.

Reviewed gateway routes also include Meta account inspection, flat-projection campaign/adset/ad/creative/image/insight collections, verified numeric object reads, and TikTok campaign/adgroup/ad/integrated-report reads. Routes are intentionally narrower than the full CLI. Graph nested expansions/aliases, unknown payloads and unverified object IDs are rejected. Listing an account-rooted collection records ownership evidence for subsequent object reads.

The `REVIEWED_HANDLERS` list is an **experimental migration gate**, not a new product goal restricting the complete CLI. Some handler options, including nested field expansions and Smart+, still fail the endpoint policy. A listed handler is not a claim that all its options passed compatibility validation.

Remaining work is tracked in `gateway-implementation-status.md`. In particular: SDK transport, uploads/downloads, complete reporting/migration/copy flows, Auth Center provider/caching, issuer login/refresh, online JWKS refresh, cross-platform service hardening and platform rate budgets are not complete.

## Server installation (trusted server only)

Use Python 3.11+ and the source from this branch:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[gateway]'
```

The gateway extra is intentionally optional: the agent CLI does not require JWT verification or cryptography dependencies. The gateway contains **only JWT public verification keys**. Keep the issuer's signing private key outside the gateway.

Create a private state directory as the service account:

```bash
motata-gateway-admin --state-dir /var/lib/motata-gateway init
motata-gateway-admin --state-dir /var/lib/motata-gateway import --ref meta-main --platform meta
motata-gateway-admin --state-dir /var/lib/motata-gateway import --ref tiktok-main --platform tiktok
```

Import prompts for the platform token with hidden input. Do not place it in argv, environment variables on the agent machine, chat, source files or logs.

Grant only explicitly intended accounts:

```bash
motata-gateway-admin --state-dir /var/lib/motata-gateway grant \
  --subject agent-a --client motata-cli --workspace single-user \
  --platform meta --account 123 --ref meta-main
```

The `workspace` is simply part of the issuer/principal binding; this branch does not add a multi-tenant SaaS. Use one fixed workspace for an internal deployment.

State is a SQLite database with authenticated encrypted credential values. `master.key` is a separate 0600 service-owned file under a 0700 directory. POSIX mode and ownership are checked, symlinks are rejected. Windows ACL enforcement is **not implemented/certified**; do not describe Windows deployments as hardened.

Copy `examples/gateway/config.example.json` and set real trusted paths, issuer, audience and the Meta version being reviewed. The example version is not a claim about the latest platform API. Install the issuer's **public** JWKS JSON at `jwks_path`, not a private JWK. The file must not be group/world-writable. Deploy changes atomically.

Run with direct TLS:

```bash
motata-gateway --config /etc/motata-gateway/config.json \
  --host 0.0.0.0 --port 8443 \
  --tls-cert /etc/motata-gateway/tls.crt \
  --tls-key /etc/motata-gateway/tls.key
```

One process/worker is required for the current global concurrency limits. Proxy headers are disabled. This increment does not support trusting an arbitrary `X-Forwarded-Proto`/identity header as proof of TLS or authentication. A reverse-proxy deployment requires an explicitly secured upstream boundary before being enabled.

For loopback-only tests, `--development-http --host 127.0.0.1` is available; it still requires JWT authentication. Unix-socket and Windows named-pipe transport are not yet implemented.

## JWT contract

Default verification algorithm: **ES256**. Explicit server configuration also supports public-key RS256 (2048 bits or larger) and EdDSA (Ed25519). HMAC and `none` are forbidden. Tests currently focus on ES256; additional algorithm compatibility evidence is still required.

Header: `typ=at+jwt`, a configured `kid`, and the configured algorithm. Unknown headers, `jku`, `x5u`, embedded `jwk`, `x5c` and critical extensions are rejected. The token never selects the JWKS location.

Required claims:

```text
iss, aud, sub, client_id, workspace_id, sid, jti, iat, exp, scope
```

`iat`, `exp`, and optional `nbf` must be integer NumericDates. Lifetime is at most 900 seconds; clock tolerance is at most 60 seconds. Use a 600-second issuance lifetime. `aud` must be the single configured audience. The gateway validates live server-side grants and revocations even while the JWT remains cryptographically valid.

Example scopes:

```text
gateway:use meta:read meta:write tiktok:read
```

Current account grants bind `(sub, client_id, workspace_id, platform, account_id)` to one credential reference. A supplied credential reference is not authorization.

The current implementation uses a trusted local JWKS snapshot, automatically reloaded when the file is replaced. Publish old+new public keys, start signing with the new key, retain the old key for the maximum token lifetime plus clock tolerance, then remove it. Emergency `kid` revocation is available without waiting for normal rotation.

```bash
motata-gateway-admin --state-dir /var/lib/motata-gateway revoke --kind sid --value session-a
motata-gateway-admin --state-dir /var/lib/motata-gateway revoke --kind kid --value compromised-key
motata-gateway-admin --state-dir /var/lib/motata-gateway remove-grant \
  --subject agent-a --client motata-cli --workspace single-user --platform meta --account 123
```

Revocation prevents later execution, including queued work. It cannot undo a platform request already sent. JWT `jti` is **not** a one-use API request ID.

## Agent CLI configuration

Remove all platform/Auth Center credential variables from the agent environment. The CLI rejects their presence in gateway mode instead of silently using them.

```bash
export MOTATA_AUTH_MODE=gateway
export MOTATA_GATEWAY_URL=https://gateway.example.com:8443
export MOTATA_GATEWAY_JWT_FILE="$HOME/.config/motata/gateway.jwt"
export MOTATA_GATEWAY_META_ACCOUNT_ID=123
export MOTATA_GATEWAY_TIKTOK_ACCOUNT_ID=789
```

The JWT file must be a private regular file (0600 on POSIX) and contain a short-lived JWT issued outside this CLI. No refresh token or signing key is needed by the gateway. The transport reloads the JWT file before each request; an external issuer/login helper may replace it atomically. A `MOTATA_GATEWAY_JWT` environment value is also supported, but remains a copyable bearer capability. Built-in issuer login/refresh is not part of this increment.

```bash
motata meta campaigns list --account 123
motata tiktok campaigns list --advertiser-id 789
```

For the reviewed Meta campaign-create route:

```bash
MOTATA_GATEWAY_IDEMPOTENCY_KEY=my-reviewed-job-step-1 \
  motata meta campaigns create --account 123 \
    --name 'Reviewed test' --objective OUTCOME_TRAFFIC
```

Use one stable key per logical write. A repeated key with an identical request reuses a safe stored result. Changed parameters cause a conflict. A timeout, interruption, unexpected platform failure, or missing authoritative result leaves a durable `pending` record and returns review-required on repetition; it does not automatically send another write. There is intentionally no agent-callable reset/force-replay endpoint. Recovery reconciliation UI/CLI remains a release blocker.

The encrypted platform token is attached only in the server's upstream authentication header. The CLI still uses an internal compatibility field named `access_token`, but in gateway mode its value is a typed `GatewayAuthRef`, **not a plaintext token or token-like placeholder string**. That compatibility slot can be removed in later refactoring after all callers migrate.

## Concurrency and safety envelope

Candidate per-instance limits:

| Limit | Default |
|---|---:|
| admitted unfinished requests | 64 |
| upstream requests in flight | 20 |
| per-account requests in flight | 2 |
| queue wait | 30 seconds |
| request JSON | 1 MiB |
| upstream JSON | 8 MiB |

There is no separate unbounded admission queue. Excess requests receive `GATEWAY_BUSY`. An account waits for its slot before occupying a global slot. Idle account semaphores are removed. Grants are checked again after queue waiting. Upstream concurrency is **not** a platform requests-per-second budget. Production app/account/endpoint rate-limit feedback and distributed quotas are still pending.

The 50-concurrency test uses a local mock upstream and validates bounded active calls, completion and cleanup. It is not a real-platform throughput measurement, a full-report capacity result, an RSS soak test or a production load certification.

## Validation

```bash
python -m pip install -r requirements-gateway-dev.txt
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_gateway.py
python scripts/check_release.py --pack-only
```

`check_gateway.py` installs a network audit guard for tests; provisioning is separate. CLI integration uses actual argparse handlers, the gateway transport, an ASGI gateway and a mocked platform, not a live ad account.

```bash
python scripts/check_gateway.py --release
```

The release gate **currently fails intentionally** because full command/network migration coverage is incomplete. Do not change that into success by marking untested routes complete or removing inventory entries.

## Security references

- RFC 8725: https://www.rfc-editor.org/rfc/rfc8725
- RFC 9068: https://www.rfc-editor.org/rfc/rfc9068
- PyJWT verification API: https://pyjwt.readthedocs.io/en/stable/api.html
- HTTPX transport testing: https://www.python-httpx.org/advanced/transports/
