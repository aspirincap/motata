# Gateway implementation status

**2026-09-17 — experimental first increment. Full project NOT complete. No release, merge, server deployment, credential import or real platform write was performed.**

## Baseline

- Repository: `aspirincap/motata`.
- Feature branch: `feat/jwt-gateway-token-isolation`.
- Original source: `55f8fd452063001b2b608ca9e1e664fed3269b32`.
- Baseline verification commit: `0c81207d6b49535927658597ac44ed23b1742b33`.
- npm source version: `0.2.0`; current npm registry tarball was not independently downloaded for this implementation.
- Local validation: Linux, Python 3.13.5, Node 22.16.0.
- Baseline GitHub Actions: Linux, Python 3.13.15, Node 20.20.2; run `35239183219`.
- Baseline results: 339 Python tests, 11 Node tests, all passed.
- Local first-increment results: original 339 Python + 11 Node; 50 gateway tests including parameterized JWT attacks and mock 50-concurrency test; wheel/sdist/npm byte audits passed. GitHub CI is the authoritative result for the final pushed commit.

## Phase status against the accepted JWT Gateway plan

| Phase | State | Evidence / remaining gate |
|---|---|---|
| 0: baseline and inventories | partial | 249 command/alias entries; network scanner yields 58 current call candidates (56 baseline). Candidates still require semantic audit, especially vendored SDK and dynamic calls. |
| 1: protocol and transport | partial | Versioned HTTPS/JSON transport and ASGI server; actual CLI integration tested. UDS/named pipe, streaming protocol pending. |
| 2: authentication and credentials | partial | Strict asymmetric JWT, local trusted JWKS reload, live grant/revocation, encrypted direct-token store and offline administration. Auth Center provider/cache/singleflight, secure OS backends, login/refresh and network JWKS cache pending. |
| 3: upstream policy and response safety | partial | Explicit reviewed endpoint set; no arbitrary origin/header/code/path execution; opaque Meta pagination; secret safety; bounded upstream slots; durable write receipts. Full endpoint review, byte-stream adapters and real platform rate budgets pending. |
| 4: Meta reads | partial | Account and flat account-rooted collection requests, verified object reads. CLI campaign listing tested. Nested projections, asset relations, platform async jobs and full report coverage pending. |
| 5: Meta writes/media | partial | Reviewed campaign create + stable-key result replay tested; existing PAUSED default preserved. Other writes, live probes and media uploads pending. |
| 6: TikTok reads | partial | Raw campaign/adgroup/ad/integrated-report routes; existing CLI campaign list tested without SDK initialization. SDK read paths, Smart+, GMV Max and other collections pending. |
| 7: TikTok writes/media | not started | All SDK writes and uploads explicitly unavailable in gateway mode; direct mode unchanged. |
| 8: reports/migration/copy | not started | Existing direct-mode regression preserved; complete gateway transport migration and recovery tests required. |
| 9: agent workflow/docs | partial | Startup rejects platform secret environment/flags; token helper refuses gateway mode; gateway runbook added. Complete canonical skill migration and init/auth UX pending. |
| 10: stress/OS hardening | partial | JWT attacks, cross-account checks, write uncertainty, queue limits, live grant removal and 50 mock calls tested. Full RSS/FD soak, uploads, multi-host quotas and real OS-identity attack tests pending. |
| 11: packaging/release | partial | Optional gateway extra, Python entrypoints and source/npm inclusion; local pack-only audits pass. Production installation/rollout and release remain blocked. |

## Deliberate deviations / clarified contracts

1. The first increment uses a trusted local JWKS file, not a token-controlled URL or an unfinished network JWKS refresh implementation. Replacing the file reloads verification keys; revoked `kid` is checked in live state.
2. Current object authorization relies on ownership evidence from reviewed account-scoped listing responses. Unknown object IDs fail before credential decryption. Later discovery may need a bounded read-only ownership lookup after coarse account authorization. The old plan's universal “zero token lookup for every unknown object” cannot be required simultaneously with discovering remote ownership; document the lookup exception before implementing it, and still require zero requested writes on authorization failure.
3. AuthContext retains an internal compatibility slot named `access_token`, populated with a typed non-secret reference in gateway mode. No real platform token is present there.
4. `REVIEWED_HANDLERS` is an experimental migration guard. The goal remains full CLI business parity; do not remove unsupported commands or claim that handler admission equals full option coverage.
5. JWT `jti` is reusable for normal calls. Durable mutation idempotency uses a separate caller/account-bound key and request fingerprint. Pending/uncertain writes cannot be automatically reset/replayed.
6. Concurrency slots are not a requests-per-second limiter. The current 50-call test is a mock test, not live throughput evidence.
7. The gateway owns encrypted platform secrets only. TLS private keys are transport credentials, while the JWT issuer's signing private key must remain outside the gateway.

## Required next implementation sequence

- [ ] Review every command and network candidate; mark only demonstrated coverage complete. Preserve option/alias drift checks.
- [ ] Add trusted Auth Center credential provider, provider-version-aware cache, per-key singleflight, expiry/invalidation tests and fail-closed configuration.
- [ ] Add issuer login/short-lived token acquisition contract and safe renewal; never move issuer signing keys or broad refresh credentials into the model context.
- [ ] Finish Meta typed transport and relationship-safe fields/objects; prevent Graph field aliases/expansions from poisoning ownership or crossing account grants.
- [ ] Migrate TikTok vendored SDK transport without fake token strings or direct-network fallback; account for its thread-pool lifecycle.
- [ ] Add bounded, backpressured upload/download adapters. Do not provide server-side arbitrary file access or pass credential-bearing signed URLs to the client.
- [ ] Migrate reports, copy and migration ledgers; retain partial/completeness and unknown-write semantics.
- [ ] Add platform app/account/endpoint rate budgets, feedback and bounded read retry ownership. Do not generically retry writes.
- [ ] Implement trusted manual receipt reconciliation and key rotation/recovery procedures; do not expose a bypass-reset endpoint to agents.
- [ ] Finish canonical `registry/skills/` and init flows.
- [ ] Validate TLS deployment, alternative IPC, Windows ACLs/macOS service identity, memory/FD soak and packaging installation in clean environments.
- [ ] Only then pass `python scripts/check_gateway.py --release` and consider production deployment.

## Commands run locally

```bash
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/gateway_inventory.py --write
python scripts/check_gateway.py
python scripts/check_release.py --pack-only
```

All platform traffic in tests is mocked. No real token or customer account data is part of the fixtures. Do not use passing unit tests as a production security certificate.
