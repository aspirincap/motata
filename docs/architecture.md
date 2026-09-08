# Architecture after hardening

## Runtime and dependency direction

```text
bin/*.js -> lib/runtime.js -> isolated Python runtime -> __main__.py
Python console scripts -------------------------------> __main__.py

CLI registration / command handlers
  -> platform services and payload builders
  -> platform client (Meta Graph / TikTok SDK + raw HTTP)

common/{auth,config,errors,utils,security,display,output}
  has no dependency on platform commands
```

Meta/TikTok package initializers lazily register CLI commands; importing a client or service no longer initializes the entire command tree. Historical command helper names remain compatibility adapters, not the implementation source.

## Modules

- `common/errors.py`: typed CLI failures and exit code.
- `common/security.py`: diagnostic redaction for URLs, headers, nested JSON, cookies and known secret values. Never mutates successful request/response data.
- `common/auth.py`, `config.py`, `utils.py`: credential context, local configuration and shared validation/JSON helpers.
- `common/display.py`: JSON/plain/table rendering; `meta/output.py` remains an import compatibility shim.
- `common/output.py`: result completeness, pagination metadata and bounded **read-only** retries. `report/completeness.py` is a compatibility import shim.
- `meta/services/{resources,creatives,media,discovery,migration_assets,migrate,migration_state}.py`: platform business operations, export/media helpers and durable execution. No reverse imports from commands.
- `tiktok/command_groups/registration.py`: parser tree, including aliases and default safety statuses.
- `tiktok/payloads.py`, `services/{normalization,discovery,copy_payloads,campaign_copy}.py`: payload construction, account/template discovery and copy orchestration.
- `tiktok/commands.py`: compatibility facade, handlers and remaining command-level orchestration. Explicit dependency adapters preserve legacy monkeypatch/call signatures; services do not import this facade.
- `report/{meta,tiktok}.py`: source collection, windows, evidence files and manifest completeness.
- `report/gmv_max_html.py`: packaged GMV Max renderer; `report render-gmv-max` is the CLI entry. The old script delegates to it.
- `registry/skills/`: the five canonical public skill sources. Root copies, local `.agents` installs and `skills/` symlinks are not inputs.

## Four distinct product responsibilities

1. **Collect:** fetch source data and persist evidence/coverage. A manifest is not a report.
2. **Diagnose:** apply metric/vertical rules and contextual reasoning. Platform attribution is not causal proof.
3. **Render:** produce a checked HTML artifact. Offline rendering does not mean remote image URLs will remain valid forever.
4. **Execute:** perform approved remote mutations. Report generation does not authorize budget changes.

Product intake produces normalized facts and strategy hints. Budget allocation, hypotheses and experiments remain explicit agent planning work, not an undocumented CLI guarantee.

## Registry compatibility

Registry builds reject symlinks, non-allowlisted file formats and known secret signatures before replacing generated skill output. The index includes SHA-256 per published file. `update` records a registry snapshot fingerprint and compares it with the current registry on explicit checks. Snapshot equality is **not** verification of installed local bytes; unknown/unavailable checks remain unknown.

## Release boundaries

Source, wheel/sdist, npm and registry are distinct artifacts. `scripts/check_release.py` audits resource bytes and metadata, rebuilds a wheel from sdist, and can install both wheel and npm tarball against a supplied offline wheelhouse. Development tests and canonical registry sources belong to the repository, not the npm runtime payload.

The vendored SDK retains its source copyright declarations and standard MIT notice. Its exact upstream revision is not yet verified; refreshing it requires provenance review. Dependency versions for CI/build are pinned in `requirements-release.txt`; application requirements remain compatible ranges.
