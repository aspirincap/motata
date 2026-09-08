# Offline release verification

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
