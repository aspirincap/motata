# Gateway implementation status

**2026-09-19 — requested SDK/BC/Smart+/GMV Max/report/Page/download/media capability
increment. Actual local source changes; no GitHub push, package publication, merge,
server deployment or live advertising operation is claimed.**

## Baselines

- Original repository/CLI: `aspirincap/motata`, `55f8fd452063001b2b608ca9e1e664fed3269b32`.
- Last remote implementation baseline: `1709e4bee9264ee2e84ebdfe49a2861690387153`,
  tree `6581324ed68b9e90b399d50b7335e0c1c4c1281a`; Auth Center integration retained.
- Immediate delivered-source baseline: preceding rebuilt archive, tree
  `698625788e485fdadb0c5de19d19b22d9c4930c5`. This increment is applied on top of it,
  not a rename of the old remote CI snapshot.
- Software version remains `0.2.0`; no release version increment.
- Local environment: Linux / Python 3.13.5 / Node 22.16.0.
- Baseline Gateway suite: 157 independent tests. Current suite: **213 tests**,
  including **56 newly added independent tests**. Tests prohibit real socket network.
- Exact final command exit codes, timings, logs, patch application and source-tree
  checks are delivered in `verification/results.json`; listing a command here alone
  is not evidence that it ran successfully. No local result is called remote CI.

See [current capability completion and upgrade runbook](gateway-completion.md).
[Previous rebuild notes](gateway-rebuild.md) are historical, including their old
limits and incomplete-feature descriptions.

## Requested six groups: implemented and fixture-verified

| Group | Current source / evidence |
|---|---|
| SDK / BC | All 50 business SDK builders referenced by TikTokClient map to reviewed routes; 1 OAuth inventory builder replaced by grant-only account discovery. 32 raw method/path pairs audited. BC/catatalog/asset metadata filtered by explicit bindings and live account grants. |
| Smart+ / GMV Max | Three-level Smart+ CRUD/status, singular/plural result evidence, material overview/breakdown, store/product/video/campaign/report paths and multi-advertiser grants; SDK/raw request-building regression. |
| Full reports | Actual nonempty Meta full/deep incl. application metadata and Page story fallback; TikTok full auction/Smart+ and GMV Max, comparison windows, requested parameters, product and creative rows; completeness asserted. |
| Page derived credentials | Server-only expiring Page leases, parent credential version/digest and session/account binding; no token in output/SQLite/agent; read-only refresh. Shared Page evidence separated from exclusive ad-object ownership. |
| Downloads | Server-issued references, public-IP connection pinning, original-host TLS, approved redirects, no credentials/cookies to CDN, private bounded spooling with complete integrity/secret checks before output, atomic CLI writes and cancellation cleanup. |
| Complete existing media migration | Actual export→run→resume with image original, video+cover, existing-post and Campaign/Adset/Ad creation; expired media reference renewal, no duplicate confirmed writes and unknown-write quarantine. Actual TikTok copy fetches/reuploads cover and keeps DISABLE defaults. |

Fixtures: `gateway_tests/test_completion_pages_downloads.py`,
`test_completion_tiktok.py`, `test_completion_workflows.py`.
Source-routing manifest: `docs/gateway-endpoint-coverage.json`.

## Important upgrade and behavior changes

1. Trusted service administrator must run `motata-gateway-admin --state-dir ... init`
   on existing state. This adds `asset_membership` without deleting credentials,
   grants or durable receipts. Startup fails closed without that upgrade.
2. BC/catalog operations require explicit administrator-verified BC membership.
   `bc/get/` cannot reveal every BC accessible by a broad provider token. Advertiser
   assets are intersected with current caller grants, not turned into new grants.
3. Page references expire in at most 300 seconds; media references in 900 seconds.
   They are in-memory, session/account-bound and reauthorized. Restart/expiry needs
   metadata refetch; unconfirmed media migration does this before downloading.
4. Images prefer the reauthorized original image_url; thumbnail is a fallback.
5. Upload/download use private complete spooling, not zero-disk proxying. Defaults:
   4 GB decimal per file, 4 concurrent streams for each direction, separate 8 GB
   spool budgets. Size acceptance is not a measured 4 GB bandwidth guarantee.
6. `changelog/task/create/` requires read scope but keeps mutation receipts; a
   report-preparation job is not permission to modify advertising objects.
7. Dynamic/shared assets do not weaken account grants, resource verification,
   redirect/origin controls or the no-plaintext-fallback rule.

## Project-wide release status (separate from the six requested groups)

The strict `--release` gate is retained and remains blocked while the entire
command/option/network ledger lacks exhaustive completion evidence. Entries
labeled `fixture_verified` mean only the named end-to-end fixture, not every
possible platform/account option combination. No blanket `complete` relabeling.

| Area | Status |
|---|---|
| JWT, direct store, Auth Center provider | Existing strict JWT and provider/cache/grants/revocations preserved. Device login/refresh CLI and online JWKS from preceding rebuild preserved. |
| Reviewed SDK/raw route coverage | All current referenced TikTok business SDK routes and concrete raw calls pass dedicated source audit. |
| Report/Page/media workflow regression | Above requested flows implemented, nonempty mock scenarios pass; live account/API schema acceptance still separate. |
| Full CLI inventory | 252 command/alias entries, 60 network candidates; structural drift checked. Fixture evidence recorded for actual CLI workflows, remaining per-option review tracked. |
| Protocol | HTTPS JSON, binary upload, authenticated binary download implemented; UDS/named-pipe deployments not implemented. |
| Agent workflow | Existing device login/status/local logout and canonical Skills retained; interactive Gateway init/install UX remains separate work. |
| Production | Not certified; no real TLS/OS-identity/Windows ACL deployment test, long RSS/FD soak, distributed limiter or live quotas proven. |

## Remaining work not represented as completed

- Actual external issuer device/token/refresh backend and authoritative Auth Center
  session/grant/binding/version/revocation contracts. This repository's CLI client
  is not a deployed issuer. Existing browser JWT remains incompatible.
- Full CLI command/option evidence, including startup/init and specialized variants
  not covered by these fixtures; unknown Graph aliases/traversals still rejected.
- Trusted mutation receipt reconciliation with no caller force-replay bypass.
- Deployment acceptance: real HTTPS/CDN/platform environment, service identity,
  Windows ACL/macOS service setup, long-running memory/FD tests and installed artifacts.
- Platform feedback-aware/distributed quota coordination. Current limits are per
  Gateway process; concurrency is not RPS and neither is a platform quota promise.

Original CLI limitations are not silently rebranded as Gateway features: Meta
migration currently supports image/link, video and existing-post shapes; it does
not newly reconstruct arbitrary dynamic/unsupported creatives. TikTok copy still
has no durable workflow resume ledger and retains partial-object error output.

## Reproduce

```bash
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_gateway.py
python scripts/check_gateway_endpoints.py
python scripts/check_release.py --pack-only
python scripts/build_skill_registry.py
python scripts/check_gateway.py --release  # strict project-wide coverage failure remains visible
```

All test secrets are synthetic. No credentials or customer data were imported,
requested or accessed to run this increment's tests.
