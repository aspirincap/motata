# Offline release verification

## OIDC npm releases

The public package is `motata`; its repository metadata must match
`git+https://github.com/aspirincap/motata.git`. Publication runs only in the
GitHub-hosted `npm-publish.yml` workflow, using a matching `v<version>` tag whose
commit is contained in `main`. It does not consume `NPM_TOKEN`, `NODE_AUTH_TOKEN`
or a developer's `.npmrc`.

Configure the existing npm package's Trusted Publisher once:

| Setting | Value |
| --- | --- |
| Provider | GitHub Actions |
| Organization or user | `aspirincap` |
| Repository | `motata` |
| Workflow filename | `npm-publish.yml` |
| Environment name | Leave empty (the workflow does not use an environment) |
| Allowed actions | Enable direct `npm publish` |

New publisher configurations default to staged publishing; direct publish must
be explicitly enabled. This account-level setup requires an authenticated npm
maintainer with 2FA. See [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/).
Do not replace OIDC with a token if configuration is missing.

The tag workflow calls all release checks before granting its publish job
`id-token: write`. Python 3.11/3.13 Linux checks, Windows regression and a separate
Node 24.22.3 / npm 12.2.2 installation job must pass. The npm 12 job exports the
audited artifacts with SHA-256 sums and uploads them for the publish job; the
publish job verifies the sums and publishes that exact tarball with provenance.
After publication it installs the exact registry version and checks `latest`.
Create the GitHub Release from `docs/releases/<version>.md` after verification.

For local npm 12 acceptance, provision Node compatible with npm 12 and run:

```sh
python3 scripts/check_release.py --wheelhouse /path/to/wheels --artifact-dir /tmp/motata-release-artifacts
python3 scripts/check_npm12_install.py --tarball /tmp/motata-release-artifacts/motata-0.3.0.tgz --version 0.3.0 --wheelhouse /path/to/wheels
```

The artifact directory must be empty. The installation script uses a temporary
global prefix and empty npm configuration, without `--ignore-scripts` or script
approval overrides. It requires npm 12 to skip postinstall by default, then
verifies the first CLI launch bootstraps Python, subsequent launches reuse it,
help/capability commands work, and updates retain the npm channel. These checks
do not contact advertising APIs. The published package can be checked using
`--package motata@0.3.0` instead of `--tarball`.

Trigger a release by pushing its tag after pushing the reviewed source to main.
`workflow_dispatch` also supports an existing version tag for retrying a failed
pre-publication run. npm versions are immutable: if publishing succeeded but a
later verification failed, repair and rerun verification separately rather than
trying to publish the same version again.

## Distribution contract

- `package.json`, `pyproject.toml`, `motata_cli.__version__`, and the current entry in `motata_cli/skills_compatibility.json` must agree.
- Python requires **3.11 or newer**. The compatibility JSON is setuptools package data and must survive both wheel and sdist builds and npm packaging.
- npm launchers install into their package-local `.runtime/venv`. A runtime is reusable only if its version marker and isolated Python import/resource health check pass. Missing markers, incomplete pip installs, stale versions, and broken dependencies trigger recreation; failures never produce a ready marker.
- Bootstrap runs in the package directory. CLI execution preserves the caller's working directory and supplies `MOTATA_INSTALL_METHOD=npm`, so update routing does not depend on installed `site-packages` paths or interpreter symlinks. Other values do not override normal detection.

## Local checks (no installs or remote calls)

With Python build/test dependencies and Node already available:

```sh
node --test tests/runtime.test.js tests/runtime-lock.test.js
python3 scripts/run_offline_tests.py
python3 scripts/check_release.py --pack-only
```

The update tests deny urllib, socket connections, and external subprocess execution unless explicitly mocked. Node runtime tests mock venv creation and pip; concurrency tests launch independent Node processes using a fake Python executable and no network. No real runtime is installed by these tests.

`check_release.py` copies explicitly allowed distribution source files into a temporary directory, builds wheel/sdist and runs `npm pack --offline --ignore-scripts`. It never opens the checkout's `.npmrc`, `token.txt`, `.runtime`, or `outputs`. Empty npm configuration paths avoid user/global npm credentials. The gate audits all three artifact inventories and prints their file counts, rejects forbidden paths and common private-key/token signatures, verifies all Python source/data files are present byte-for-byte, checks package metadata versions and Python requirements, and rebuilds a wheel from the sdist. Temporary artifacts are removed afterward. This is a distribution-source gate, not a credential scan of the entire checkout.

## CI installation smoke

`.github/workflows/release-check.yml` runs on Python 3.11 and 3.13 with Node 20. Dependencies are pinned in `requirements-release.txt`; provisioning is the only network-enabled phase. A Windows Python 3.11 job runs the full Python suite and Node runtime tests in separate steps so a successful Node command cannot mask a Python failure. Windows HOME/USERPROFILE and MOTATA_HOME point to temporary test state. Linux tests and artifact/installation checks run in a network namespace without network access; pip uses `PIP_NO_INDEX=1`.

```sh
python3 scripts/check_release.py --wheelhouse /path/to/preprovisioned/wheels
```

Unlike `--pack-only`, this intentionally installs packages into temporary isolated virtualenvs: it installs the rebuilt wheel, checks its compatibility resource, runs CLI help outside the source directory, extracts the npm artifact and runs its launcher using only the supplied wheelhouse, and checks npm update-channel detection. The wheelhouse must include runtime and build dependencies (including setuptools and wheel and their transitive dependencies). No publish or global install is performed.

Without either option, the gate performs only a dependency-free isolated wheel installation/resource smoke and explicitly reports that CLI help smoke was skipped. Use `--wheelhouse` for the full release gate.

## Source boundaries

Only the named report/registry/release scripts are unignored; `scripts/npm-postinstall.js` remains the npm bootstrap source. The optional report script allowlist is the singular `generate_motata_html_report.py`; unrelated plural/local scripts remain ignored. Cloudflare permits only its package manifests and `wrangler.toml`, not generated `public/` or dependencies. Canonical skill sources live under `registry/skills/`; local agent skill installations remain ignored. Registry generation and canonical skill maintenance are separate from this distribution gate.
