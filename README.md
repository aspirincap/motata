# motata-cli

`motata` is a modular ads operations CLI for Meta and TikTok.
It is designed for real account work: onboarding, asset discovery, validation, reporting, migration, and cross-account analysis.

## What It Covers

`motata` focuses on the workflows that usually break in production ad accounts:

- token-aware onboarding with `motata init`
- ad-account and advertiser discovery
- asset discovery for Pages, identities, apps, pixels, and promoted objects
- prelaunch validation before writes
- Meta and TikTok reporting
- user-type classification and recommended metrics
- product-page intake for cold-start planning
- cross-account migration and resumable execution flows

The CLI is self-contained:

- install from PyPI with `pip install motata-cli`
- install from npm with `npm install -g motata`
- use bundled helpers such as `motata-token` and `motata-auth-center-token`

The npm package bootstraps a local Python virtualenv during install, so `python3` must be available on the target machine.

## Installation

From PyPI:

```bash
pip install motata-cli
```

From npm:

```bash
npm install -g motata
```

From source:

```bash
pip install -e .
```

Python `3.11+` is required.
Node `18+` is required for the npm distribution.

## Update and Skill Sync

To update the CLI and sync the published Motata skills in one step:

```bash
motata update
```

Useful variants:

```bash
motata update --check
motata update --cli-only
motata update --skills-only
```

Notes:

- default skills source: `https://skill.motata.one`
- compatible legacy alias: `https://motata-skills.pages.dev`
- interactive terminals warn when required skills are missing or out of sync

## Start With `motata init`

If token source, account scope, or baseline metrics are not settled yet, start with:

```bash
motata init
```

The init flow is the recommended onboarding entry because it confirms execution prerequisites before deeper workflows begin.

### What `motata init` does

`motata init` now performs one guided onboarding flow:

1. confirm token source: direct token or Motata Auth Center
2. require token input before continuing
3. confirm the target platform account
4. allow keyboard selection instead of forcing manual input
5. allow multi-select account picking for Meta and TikTok
6. fetch user-type classification and show a "getting recommended metrics" step
7. let the user keep or override recommended metrics
8. save a suggested daily-report command as a starting point; choose the actual next workflow by scenario

### Interaction model

The flow supports both interactive and non-interactive usage:

- keyboard selection for platform, token source, and accounts
- multi-account selection by keyboard when accounts are listed
- direct input of multiple accounts with comma-separated IDs
- language control with `--lang auto|zh|en`

Examples:

```bash
motata init \
  --platform meta \
  --token-source direct \
  --account-id 1234567890
```

```bash
motata init \
  --platform tiktok \
  --token-source auth-center \
  --advertiser-id 7444033053753835536
```

## Init Contract

`motata init` is an onboarding step, not a secret store.

### `motata init` saves

- default platform
- default account for that platform
- selected account list for that platform
- detected user-type summary
- recommended analysis metrics
- a suggested next command

### `motata init` does not save

- platform access tokens
- Auth Center API keys
- secrets copied from runtime environment variables

### Commands that automatically reuse init state

Today, these commands can automatically reuse init state:

- `motata report meta run`
- `motata report tiktok run`

Automatic reuse currently includes:

- default account fallback when the account flag is omitted
- initialized account list when `--all-init-accounts` is used
- recommended metrics stored by init

### Multi-account rule

Recommended metrics are inferred from the full selected account set, not just the first account:

- Meta recommended metrics are based on all selected Meta accounts
- TikTok recommended metrics are based on all selected TikTok advertisers
- the first selected account is still stored as the default follow-up account

## Authentication

`motata` supports two authentication paths.

### 1) Direct token

Pass a platform access token directly:

```bash
motata meta campaigns list \
  --account 766290062019765 \
  --access-token "$META_ACCESS_TOKEN"
```

You can also provide tokens through environment variables:

- `META_ACCESS_TOKEN`
- `MOTATA_META_ACCESS_TOKEN`
- `TIKTOK_ACCESS_TOKEN`
- `MOTATA_TIKTOK_ACCESS_TOKEN`

### 2) Motata Auth Center

Use an Auth Center API key to fetch a platform token, then pass the resulting token into `motata`.

Standalone helper:

```bash
motata-token \
  --mode account \
  --account-id 1015303836971442 \
  --api-key "$AUTH_CENTER_API_KEY"
```

Legacy alias:

```bash
motata-auth-center-token \
  --mode account \
  --account-id 1015303836971442 \
  --api-key "$AUTH_CENTER_API_KEY"
```

Then use the token in the main CLI:

```bash
motata meta campaigns list \
  --account 1015303836971442 \
  --access-token "$META_ACCESS_TOKEN"
```

TikTok follows the same pattern:

```bash
motata tiktok campaigns list \
  --advertiser-id 7444033053753835536 \
  --access-token "$TIKTOK_ACCESS_TOKEN"
```

## Scenario Routing

After init, do not assume the next step is always a daily report.
The right next command depends on the business scenario.

### 1) New account takeover

Use when you need to know:

- can this account run
- what assets are usable
- what advertiser type it belongs to

Typical entry points:

- `motata meta assets discover`
- `motata tiktok accounts list`
- `motata meta user-type analyze`
- `motata tiktok user-type analyze`

Typical outputs:

- usable assets
- missing prerequisites
- abnormal bindings
- account-type tags
- next analysis path

### 2) Prelaunch risk check

Use when page, pixel, app, identity, or creative relationships may be invalid.

Entry points:

- `motata meta validate ad-link`
- `motata tiktok validate promoted-object`

Outputs:

- go / no-go result
- exact failure point
- next safe fix step

### 3) Product cold-start planning

Use when you only have a product page or app page and need a same-day test plan.

Entry points:

- `motata product scrape`
- `motata product intake`

CLI outputs:

- extracted product/page facts
- value-proposition candidates
- objective and asset-requirement hints

A budget split, audience hypothesis and testing matrix are separate planning work performed by the agent with user-confirmed goals; the intake command does not generate or validate a complete launch plan.

### 4) Page or SKU diagnosis

Use when multiple URLs or SKUs are running and you need to know what should scale or stop.

Typical outputs:

- spend ranking by URL or SKU
- click and conversion comparisons
- page-priority recommendations

### 5) Daily anomaly check

Use when you want a yesterday-first review.

Entry points:

- `motata report meta run --period daily`
- `motata report tiktok run --period daily`

CLI outputs source JSON and a completeness manifest. Anomaly explanations and proposed actions are a separate diagnosis step; do not automate budget changes from degraded or truncated data.

### 6) Weekly review

Use when preparing a client or leadership report.

Entry points:

- `motata report meta run --period weekly`
- `motata report tiktok run --period weekly`

The `report ... run` commands collect source JSON and a manifest, including current/previous windows and completeness. HTML rendering and recommendation writing are separate steps.

For GMV Max runs, an installed renderer is available:

```bash
motata report render-gmv-max --run-dir /path/to/run --out /path/to/report.html
```

Rendering is offline by default (existing remote image URLs may still load in a browser). Use `--cache-images` only to explicitly allow image downloads. Other report types use the Motata report skill; a manifest alone is not a finished HTML artifact.

### 7) Creative fatigue detection

Use when the creative team needs replacement priority.

Outputs:

- fatigue leaderboard
- high-spend low-conversion watchlist
- replacement priority

### 8) Bottleneck troubleshooting

Use when spend dropped and the team does not know whether the block is budget, review, status, identity, creative, or platform constraints.

Outputs:

- bottleneck location
- root-cause category
- next diagnostic step

### 9) Multi-account standardization

Use when many accounts need the same inventory, report, and analysis flow.

Useful pattern:

- run `motata init`
- confirm multiple accounts
- reuse them through `--all-init-accounts`

Outputs:

- scale / stable / risk tiers
- normalized review flow
- repeatable team SOP

### 10) Migration and rebuild

Use when an account is restricted or needs to move across entities.

Entry points:

- `motata meta migrate export`
- `motata meta migrate plan`
- `motata meta migrate run`
- `motata meta migrate resume`

Outputs:

- migration plan
- precheck results
- versioned checkpoint and source-to-target ledger
- confirmed IDs, uncertain writes and plan-only cleanup inventory

Use the same `--job-id` with `resume`. Confirmed operations are skipped; uncertain writes stop in `needs_review` and require manual reconciliation. Names are never trusted as idempotency keys. Legacy jobs without a ledger cannot safely resume. Migration creates PAUSED objects and does not run hidden create/delete probes or automatic cleanup.

## Safety, Compatibility and Development

- Exit codes: `0` success, `1` failed/needs review, `2` argument error, `3` partial/degraded. Report manifests remain available after partial failure.
- `PAUSED` / `DISABLE` still creates real remote objects. Meta live validation requires `--allow-live-probe`; cleanup is best effort. TikTok copy/bootstrap now defaults all created levels to `DISABLE`.
- Pagination limits are explicit. Monetary aggregation and rankings remain within a currency; unknown currencies remain account-isolated. No automatic FX conversion is performed.
- npm runtime checks Python >=3.11, repairs incomplete installs, serializes initialization, and preserves the caller's working directory.
- Published skills have a single source: `registry/skills/`. Root copies, `skills/` symlinks and local agent installations are not release sources.
- Version 0.2.0 is prepared as an unreleased candidate on this branch. Review the [0.2.0 release notes and upgrade guidance](docs/releases/0.2.0.md); test/build commands do not publish packages or the registry.

```bash
python3 scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python3 scripts/check_release.py --pack-only
python3 scripts/build_skill_registry.py
```

See [execution contracts](docs/execution-contracts.md), [architecture](docs/architecture.md), [release engineering](docs/release-engineering.md), and [validation results](docs/hardening-validation.md).

## Quick Start Examples

### Product intake

Scrape a product or app page into normalized inputs:

```bash
motata product scrape \
  "https://www.anker.com/products/a1695-anker-power-bank-25000mah-165w"
```

Build a strategy-ready intake:

```bash
motata product intake \
  "https://www.anker.com/products/a1695-anker-power-bank-25000mah-165w"
```

### Meta asset discovery

```bash
motata meta assets discover \
  --account 766290062019765 \
  --access-token "$META_ACCESS_TOKEN"
```

### Meta prelaunch validation

```bash
motata meta validate ad-link \
  --account 766290062019765 \
  --access-token "$META_ACCESS_TOKEN" \
  --adset-id <ADSET_ID> \
  --creative-id <CREATIVE_ID> \
  --allow-live-probe \
  --cleanup
```

### Meta user-type classification

```bash
motata meta user-type analyze \
  --access-token "$META_ACCESS_TOKEN" \
  --date-preset last_14d \
  --account-limit 10 \
  --campaign-limit 10
```

### TikTok user-type classification

```bash
motata tiktok user-type analyze \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --advertiser-limit 10 \
  --campaign-limit 10
```

### Daily report using init defaults

```bash
motata report meta run \
  --period daily \
  --access-token "$META_ACCESS_TOKEN"
```

```bash
motata report tiktok run \
  --period daily \
  --access-token "$TIKTOK_ACCESS_TOKEN"
```

### Batch reports for all initialized accounts

```bash
motata report meta run \
  --period weekly \
  --all-init-accounts \
  --access-token "$META_ACCESS_TOKEN"
```

```bash
motata report tiktok run \
  --period weekly \
  --all-init-accounts \
  --access-token "$TIKTOK_ACCESS_TOKEN"
```

### Explicit multi-account report runs

```bash
motata report meta run \
  --account-id 1234567890,2345678901 \
  --period weekly \
  --access-token "$META_ACCESS_TOKEN"
```

```bash
motata report tiktok run \
  --advertiser-id 7444033053753835536,7444033053753835537 \
  --period weekly \
  --access-token "$TIKTOK_ACCESS_TOKEN"
```

## Notes for Agent Runners

If an agent is using `motata`, this is the default decision sequence:

1. if token source, account scope, or metrics are unclear, run `motata init`
2. if the input is a product or app URL, consider `motata product intake`
3. if the goal is readiness or safety, prefer asset discovery or validation before writes
4. if the goal is diagnosis or review, prefer reporting plus user-type or account analysis
5. if multiple accounts are involved, prefer inventory plus batch report flows over one-account manual loops

A direct jump from init into a daily report is valid, but it is only one use case.
After init, the next step should still be chosen by scenario: takeover, validation, diagnosis, reporting, migration, or cold-start planning.

## Command Discovery

Explore the command tree with:

```bash
motata --help
motata init --help
motata meta --help
motata tiktok --help
motata report --help
```

## Design Principles

`motata` is intentionally biased toward:

- account portability
- debuggability
- batch safety
- migration reliability
- explicit validation before writes

It does not implement browser-based OAuth flows.
