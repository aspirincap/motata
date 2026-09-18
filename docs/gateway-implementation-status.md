# Gateway implementation status

**2026-09-18 — Auth Center credential-provider increment. Full CLI migration is
NOT complete. No release, merge, production deployment or real platform operation
was performed.**

## Baseline and evidence

- Repository: `aspirincap/motata`.
- Branch: `feat/jwt-gateway-token-isolation`; draft PR #1.
- Original CLI baseline: `55f8fd452063001b2b608ca9e1e664fed3269b32`.
- Previous implementation: `0ddc76d23561c23c062a155bb950f284ac1cde5c`.
- Previous remote CI: 339 Python + 11 Node + 50 gateway tests passed; gateway run
  `35246002906`, existing release regression run `35246002775`.
- Current local tests: **339 Python + 11 Node + 94 gateway tests passed** (44 new
  Auth Center/configuration/pipeline tests). Pack-only wheel/sdist/npm audits pass.
- Current environment: Linux, Python 3.13.5, Node 22.16.0. Upstream calls mocked;
  test runner prohibits real network access. Source npm version remains `0.2.0`.
- CI for the actual pushed commit is the authoritative post-push evidence. This
  document does not claim a CI result before that run completes.
- `python scripts/check_gateway.py --release` still intentionally fails because
  command/network migration coverage remains incomplete.

## This increment

Implemented server-only `AuthCenterProvider` and async `CredentialResolver` using
existing Auth Center OpenAPI at pinned source `96fda9454c02cc15608d89a41333fe8e4283c5d1`:

- Fetch exact `{channel}:{oauth_agent_id}`; no account/channel-wide token fallback.
- Require explicit administrator-verified tenant/platform/account/OAuth binding;
  reject cross-workspace/account grants. This is an administrator assertion,
  **not automatic remote account membership verification**.
- Protected profile/API-key files, fixed HTTPS origin, no redirects, caller JWT,
  inherited cookies/headers or proxy environment sent to Auth Center.
- Bounded short-lived positive/negative cache, per-key singleflight, key-file
  rotation, expiry buffers, invalidation, cancellation and service shutdown.
- Default positive cache TTL 15 seconds, maximum 60; **remote revocation/quota
  changes are not immediate while a lease is cached**. Local grants/revocation
  are rechecked before dispatch even with a warm cache.
- Recheck JWT/grants/binding after external IO. Credential-fetch failure creates
  no pending write receipt; completed results replay without fetching a secret.
- Non-destructive protected-state migration via offline admin `init`; new admin
  `bind-auth-center` command. No business HTTP credential export/admin endpoints.
- Fetched platform credentials and raw OAuth responses are not persisted in
  Gateway SQLite or CLI artifacts. Existing direct encrypted store still works.

See [Auth Center integration runbook](gateway-auth-center.md) for installation,
protected-state migration, exact binding requirements, cache and failure semantics.

## Phase status against accepted JWT Gateway plan

| Phase | State | Implemented / remaining |
|---|---|---|
| 0: inventories | partial | 249 command/alias entries, 58 network-call candidates; semantic full-coverage audit pending. |
| 1: protocol | partial | HTTPS/JSON + real CLI adapter; UDS/named pipe and streaming pending. |
| 2: identity and credentials | partial | Asymmetric JWT, local JWKS reload, local grants/revocations, encrypted direct store, Auth Center provider/cache/bindings. Dedicated issuer login/refresh, authoritative remote session/grant integration and online JWKS pending. |
| 3: request safety | partial | Reviewed endpoints, fixed destinations, bounded slots, secret safety, opaque pagination and durable receipts. Full endpoint review, streaming and actual RPS budgets pending. |
| 4: Meta reads | partial | Flat account collections/verified objects; CLI campaign list tested. Nested field relationships, async jobs and full report parity pending. |
| 5: Meta writes/media | partial | Campaign creation with stable-key result replay tested; PAUSED default preserved. Remaining writes/probes/uploads pending. |
| 6: TikTok reads | partial | Raw campaign/adgroup/ad/integrated-report routes; CLI campaign list tested. SDK, Smart+, GMV Max and other collections pending. |
| 7: TikTok writes/media | not started | SDK writes/uploads unavailable in gateway mode; direct mode unchanged. |
| 8: reports/copy/migration | not started | Existing direct regression preserved; full gateway workflow/recovery parity pending. |
| 9: agent workflow | partial | Startup rejects inherited platform secrets; token helper refuses gateway mode. Canonical Skills/init/login UX pending. |
| 10: stress/OS | partial | JWT attacks, cross-account checks, queue limits, write uncertainty and 50 mock calls tested; new 50-call provider singleflight/pipeline tests pass. RSS/FD soak, real TLS/OS identity/ACL acceptance pending. |
| 11: release | partial | Optional gateway dependencies, entrypoints and pack-only byte audit; production release remains blocked. |

## Clarified boundaries

1. Current Auth Center browser JWT is NOT a Gateway JWT. Keep dedicated audience,
   asymmetric verification and `typ=at+jwt`; do not weaken the existing verifier.
2. Auth Center OAuth `Agent` identities are not AI agent/CLI caller identities.
3. Auth Center token response currently does not guarantee `token_version`.
   Optional version is preserved when supplied; no fabricated event/version sync.
4. Local Gateway grants remain authoritative. No Auth Center session/grant API
   was invented or claimed deployed. Auth Center source/deployment was untouched.
5. Object ownership currently comes from reviewed account-scoped listings. Later
   discovery may require bounded read-only verification after coarse account
   authorization. Invalid JWT/grants must still cause zero credential fetches.
6. `AuthContext.access_token` retains a typed non-secret compatibility reference
   in gateway mode. No real platform token is stored in that agent-side slot.
7. JWT `jti` is reusable. Mutation idempotency is a separate caller/account-bound
   key; pending or unknown writes cannot be automatically reset or resent.
8. Concurrent slots are not an RPS limit. Passing mocks is not production
   security certification or proof of live platform throughput.

## Next work

- [ ] Add dedicated Gateway JWT issuance, CLI browser login and limited refresh
  in cooperation with the Auth Center project; preserve existing web sessions.
- [ ] Replace admin binding assertions with a strict remote binding/lease/version
  contract; add authoritative session/grant checks and bounded revocation events.
- [ ] Harden Auth Center secret storage and remove OAuth raw-data secret copies;
  do not assume a column named `*_ciphertext` is actually encrypted.
- [ ] Complete every Meta relationship/field path and TikTok vendored SDK exit.
- [ ] Add bounded streaming upload/download and video-session handling.
- [ ] Migrate full reports, copy and migration, preserving completeness and
  unknown-write/recovery contracts.
- [ ] Add platform app/account/endpoint RPS budgets and explicit retry ownership.
- [ ] Implement trusted receipt reconciliation without an agent replay bypass.
- [ ] Complete canonical `registry/skills/` and gateway-mode init/login flows.
- [ ] Validate clean TLS deployment, OS identities, Windows ACL/macOS service
  isolation, memory/FD soak and installed package behavior.
- [ ] Only then pass the full-release coverage gate and consider a production PR.

## Reproducible commands

```bash
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_gateway.py
python scripts/check_release.py --pack-only
python scripts/check_gateway.py --release  # expected incomplete-coverage failure
```

No real tokens/customer data are in fixtures. No Auth Center key was requested
from the user or imported into a running server during this implementation.
