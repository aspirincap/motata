# Execution, completeness and recovery contracts

## Process results

| Exit | Meaning | Automation behavior |
|---|---|---|
| 0 | Successful operation, or an explicitly requested plan/dry-run | Continue only within the returned scope |
| 1 | Failure or uncertain write requiring review | Stop; inspect saved results |
| 2 | CLI argument/usage error | Fix invocation; do not retry unchanged |
| 3 | Partial success / degraded evidence | Preserve artifacts; do not treat as full success |

Legacy successful handlers may return `None`; the dispatcher also accepts integer results, explicit `exit_code`, and structured status/completeness. Report files are written before the report handler returns a nonzero completeness code.

`completeness` schema version 1 contains `status` (`success`, `partial_success`, `failed`), `complete`, `exit_code`, and `reasons`. Warnings, errors and truncated pagination propagate through source and batch manifests. This is a coverage contract, not a guarantee that upstream attribution data is correct. An empty, valid dataset is not automatically an error. Dry-run's planned sources are not fetched data.

## Side-effect matrix

| Surface | Side effect | Guard / limitation |
|---|---|---|
| `--help`, `--version` | None | No state directory or remote drift check |
| account/assets/list/get/insights | Remote reads | May page, enrich and download linked data according to options |
| `report ... run --dry-run` | Local plan only | No platform calls |
| `report ... run` | Remote reads + local source files | Inspect completeness even if files exist |
| `report render-gmv-max` | Local HTML output | No new remote downloads by default; `--cache-images` opts in |
| Meta `validate creative/ad-link/promoted-object` | Real creations, optional deletions | CLI requires `--allow-live-probe`; `--cleanup` is best effort |
| Meta/TikTok create/update/status/delete/media | Real remote writes | User approval and reviewed scope required; PAUSED/DISABLE is not dry-run |
| TikTok copy/bootstrap | Real multi-object creation | All three levels default DISABLE; partial objects are retained in output |
| TikTok bootstrap `--dry-run` | Template/asset reads | No creates/updates |
| Meta migrate export/plan | Reads + local export/plan | No target creation |
| Meta migrate run/resume | Real PAUSED creation + local checkpoint/media | Versioned ledger and per-job cross-process lock |
| `update --check` | Registry/package metadata reads, local skill inspection | Does not bootstrap a skills CLI through npx |
| `update` / `--skills-only` | Package/skill installation | Explicit update command required |

The matrix does not turn direct Python function calls into an authorization boundary. Library callers must apply the same approval policy. Raw Graph writes are deliberately low-level and require the caller to review method, scope and payload.

## Monetary scope and pagination

TikTok landing reports group by currency + canonical URL. Unknown currency is kept separate per advertiser. Currency is normalized only from observed fields/account metadata; it is never guessed. When scopes are mixed, global monetary totals/ROAS are `null`, and spend ranks and top-N selection are per currency or unknown-account scope. `totals_scope=selected_landing_rows` distinguishes selected landing totals from all-ad spend.

Pagination records `truncated`, `stop_reason`, `pages_fetched` and `total_pages` (nullable). A full final page without authoritative total-page metadata is conservatively incomplete when a cap stops fetching. Metric fallback is only for explicit unsupported/incompatible metrics, not permission, auth or generic failures. Retryable reads repeat identical parameters at most three times; no generic retry is permitted for writes.

## Migration job lifecycle

Storage: `${MOTATA_HOME:-~/.motata}/jobs/<job-id>.json`.

1. Validate job ID, acquire the persistent per-job lock and load/create schema version 1.
2. Compare configuration and export fingerprint before resuming.
3. Persist a `pending` ledger entry **before** each remote write.
4. After an authoritative remote ID is returned, persist `confirmed` and the source→target mapping.
5. If a write times out, is interrupted, returns no ID or cannot be durably confirmed, stop in `needs_review`. No blind retry.
6. A subsequent `resume` skips confirmed writes. A completed job returns its saved result without remote calls.

Statuses: `running`, `failed` (safe pre-write failure), `needs_review` (uncertain mutation), `completed`. Individual ledger entries are `pending`, `confirmed`, `needs_review`. Writes use temporary-file fsync and atomic replacement; POSIX also fsyncs the directory. Locks use fcntl on POSIX and msvcrt on Windows.

The request ledger stores fingerprints, not access tokens or complete request bodies. The export and source IDs remain essential evidence; do not alter them between run and resume. Reuse-by-name flags remain parseable for compatibility but are not trusted for reuse.

### Handling needs_review

Preserve the checkpoint and export. Inspect request receipts and the target account to determine whether the operation succeeded; verify target ownership and the intended payload. A matching name alone is not evidence. There is intentionally no automatic adoption, deletion or checkpoint-reset command. A human-reviewed reconciliation is required before continuing an uncertain job; if no reliable correlation is available, stop rather than duplicate the write. Do not create a new job merely to bypass this protection.

`cleanup_plan` lists ledger objects and recovery guidance; it **never deletes** remote objects. Any cleanup is a separately reviewed operation. Legacy jobs without the versioned ledger are rejected for resume instead of silently rerun.

## Compatibility changes

- Live Meta probes require the new explicit flag.
- TikTok copy/bootstrap ads now default DISABLE instead of ENABLE.
- Partial results return 3 instead of a misleading 0.
- Cross-currency totals may be null; consumers must not coerce them to zero.
- Relative npm paths use the caller's directory rather than the package directory.
- Legacy migration resume is refused; names do not establish identity.
- GMV Max rendering is packaged and offline by default; missing previews stay missing.
